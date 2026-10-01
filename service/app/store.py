"""LanceDB store: one table per collection, hybrid search (vector + full text, fused with
reciprocal rank fusion), optional cross-encoder rerank. Falls back to full-text only when
the embedding endpoint is down (e.g. the GPU server is off)."""
import json
import logging
import os
import threading
import time

import lancedb

from .config import DATA_DIR, Settings
from .ingest import build_chunks, fingerprint
from .providers import Embedder, LLM, Reranker

log = logging.getLogger("rag.store")
STATE = os.path.join(DATA_DIR, "state.json")


class Store:
    def __init__(self, s: Settings):
        self.s = s
        self.db = lancedb.connect(os.path.join(DATA_DIR, "lancedb"))
        self.embedder = Embedder(s.embedding)
        self.reranker = Reranker(s.rerank)
        self.llm = LLM(s.llm)
        self._lock = threading.Lock()
        self.state = json.load(open(STATE)) if os.path.exists(STATE) else {}
        self.status = {}

    def reload(self, s: Settings):
        """Swap in new model settings (from the admin page) without a restart."""
        self.s = s
        self.embedder = Embedder(s.embedding)
        self.reranker = Reranker(s.rerank)
        self.llm = LLM(s.llm)

    def _save(self):
        json.dump(self.state, open(STATE, "w"), indent=1)

    def _table_names(self):
        try:
            return set(self.db.table_names())
        except Exception:
            return set()

    # ------------------------------------------------------------ indexing
    def _fp(self, name):
        col = self.s.collections[name]
        return fingerprint(col.sources, extra=f"{self.embedder.id}|{self.s.chunk_chars}|{col.anonymise}")

    def index(self, name, force=False):
        col = self.s.collections[name]
        fp = self._fp(name)
        if not force and self.state.get(name, {}).get("fingerprint") == fp and name in self._table_names():
            return {"collection": name, "status": "unchanged", "chunks": self.state[name].get("chunks", 0)}
        with self._lock:
            t0 = time.time()
            self.status[name] = "chunking"
            chunks = build_chunks(col, self.s)
            if not chunks:
                self.status[name] = "empty"
                if name in self._table_names():
                    self.db.drop_table(name)
                self.state[name] = {"fingerprint": fp, "chunks": 0, "indexed_at": time.time(), "embedder": self.embedder.id}
                self._save()
                return {"collection": name, "status": "empty", "chunks": 0}
            prog = lambda done, total: self.status.__setitem__(name, f"embedding {done}/{total}")
            vecs = self.embedder.embed_documents([c["text"] for c in chunks], progress=prog)
            for c, v in zip(chunks, vecs):
                c["vector"] = v
            tbl = self.db.create_table(name, data=chunks, mode="overwrite")
            try:
                tbl.create_fts_index("text", replace=True)
            except TypeError:
                tbl.create_fts_index("text")
            self.state[name] = {"fingerprint": fp, "chunks": len(chunks), "indexed_at": time.time(),
                                "embedder": self.embedder.id, "seconds": round(time.time() - t0, 1)}
            self._save()
            self.status[name] = "ready"
            log.info("indexed %s: %d chunks in %.0fs", name, len(chunks), time.time() - t0)
            return {"collection": name, "status": "indexed", "chunks": len(chunks), "seconds": round(time.time() - t0, 1)}

    def index_all(self, force=False):
        out = []
        for n in self.s.collections:
            try:
                out.append(self.index(n, force))
            except Exception as e:
                log.exception("index %s failed", n)
                self.status[n] = f"error: {e}"
                out.append({"collection": n, "status": "error", "error": str(e)[:300]})
        return out

    # ------------------------------------------------------------ search
    def search(self, query, collections, top_k=8):
        names = [c for c in collections if c in self._table_names()]
        if not names:
            return {"results": [], "mode": "none", "note": "no indexed collections available"}
        k = max(top_k, self.s.candidates)
        mode, qvec = "hybrid", None
        try:
            qvec = self.embedder.embed_query(query)
        except Exception as e:
            log.warning("embedding endpoint unavailable, keyword-only search: %s", e)
            mode = "keyword-only (embedding model unreachable)"
        fused = {}
        for n in names:
            tbl = self.db.open_table(n)
            lists = []
            if qvec is not None:
                q = tbl.search(qvec)
                try:
                    q = q.distance_type("cosine")
                except AttributeError:
                    q = q.metric("cosine")
                lists.append(q.limit(k).to_list())
            try:
                lists.append(tbl.search(query, query_type="fts").limit(k).to_list())
            except Exception as e:
                log.warning("fts on %s failed: %s", n, e)
            for lst in lists:
                for rank, r in enumerate(lst):
                    key = (n, r["id"])
                    e = fused.setdefault(key, {"row": r, "collection": n, "rrf": 0.0})
                    e["rrf"] += 1.0 / (60 + rank)
        cands = sorted(fused.values(), key=lambda e: -e["rrf"])[:k]
        if self.reranker.enabled and cands:
            try:
                n = max(top_k, self.s.rerank.candidates)
                head, tail = cands[:n], cands[n:]
                scores = self.reranker.rerank(query, [c["row"]["text"][:self.s.rerank.max_chars] for c in head])
                for c, sc in zip(head, scores):
                    c["rerank"] = sc
                head.sort(key=lambda c: -c["rerank"])
                cands = head + tail
                mode += " + rerank"
            except Exception as e:
                log.warning("rerank failed: %s", e)
        out = []
        for c in cands[:top_k]:
            r = c["row"]
            out.append({"collection": c["collection"], "title": r["title"], "section": r["section"], "page": r["page"],
                        "source": r["source"], "text": r["text"], "meta": json.loads(r.get("meta") or "{}"),
                        "score": round(c.get("rerank", c["rrf"]), 4)})
        # passages keep their full text; rerank only decides the order
        return {"results": out, "mode": mode}

    def ask(self, question, collections, top_k=6):
        if not self.llm.enabled:
            return {"error": "no LLM configured; use search and answer yourself"}
        res = self.search(question, collections, top_k)
        if not res["results"]:
            return {"answer": "No relevant passages found.", "sources": [], "mode": res["mode"]}
        answer = self.llm.answer(question, res["results"])
        return {"answer": answer, "mode": res["mode"],
                "sources": [{"n": i + 1, "title": r["title"], "page": r["page"], "source": r["source"]} for i, r in enumerate(res["results"])]}

    def info(self, names):
        out = []
        for n in names:
            c = self.s.collections[n]
            st = self.state.get(n, {})
            status = self.status.get(n, "ready" if st else "not indexed")
            if status in ("ready", "empty", "not indexed") and st and st.get("fingerprint") != self._fp(n):
                status = "changes pending"   # files added/changed/removed since the last index run
            elif status == "not indexed" and n in [k for k in self.s.collections]:
                status = "queued"
            out.append({"name": n, "description": c.description, "chunks": st.get("chunks", 0),
                        "indexed_at": time.strftime("%Y-%m-%d %H:%M", time.localtime(st["indexed_at"])) if st.get("indexed_at") else None,
                        "status": status})
        return out
