"""Model providers. Embeddings and the LLM are always OpenAI-compatible HTTP endpoints
(the bundled Ollama on CPU, a GPU server such as vLLM, or any hosted API).
The reranker can also run in-process on the CPU (fastembed / ONNX) or be skipped."""
import hashlib
import logging
import os
import sqlite3
import threading

import httpx

from .config import DATA_DIR, Endpoint

log = logging.getLogger("rag.providers")


def _headers(ep: Endpoint):
    h = {"Content-Type": "application/json"}
    if ep.api_key:
        h["Authorization"] = f"Bearer {ep.api_key}"
    return h


def _url(ep: Endpoint, path):
    return ep.base_url.rstrip("/") + path


class Embedder:
    """OpenAI /embeddings client with an on-disk cache keyed by (model, text)."""

    def __init__(self, ep: Endpoint):
        self.ep = ep
        self.id = f"{ep.base_url}|{ep.model}"
        os.makedirs(DATA_DIR, exist_ok=True)
        self._db = sqlite3.connect(os.path.join(DATA_DIR, "embed-cache.sqlite"), check_same_thread=False)
        self._db.execute("create table if not exists c (k text primary key, v blob)")
        self._lock = threading.Lock()

    def _key(self, text):
        return hashlib.sha256(f"{self.ep.model}\x00{text}".encode()).hexdigest()

    def _call(self, texts, timeout=None):
        r = httpx.post(_url(self.ep, "/embeddings"), headers=_headers(self.ep), timeout=timeout or self.ep.timeout,
                       json={"model": self.ep.model, "input": texts})
        r.raise_for_status()
        data = sorted(r.json()["data"], key=lambda d: d["index"])
        return [_norm(d["embedding"]) for d in data]

    def embed_documents(self, texts, progress=None):
        import numpy as np
        out, todo = [None] * len(texts), []
        with self._lock:
            for i, t in enumerate(texts):
                row = self._db.execute("select v from c where k=?", (self._key(self.ep.document_prefix + t),)).fetchone()
                if row:
                    out[i] = np.frombuffer(row[0], dtype="float32").tolist()
                else:
                    todo.append(i)
        for s in range(0, len(todo), self.ep.batch_size):
            idx = todo[s:s + self.ep.batch_size]
            vecs = self._call([self.ep.document_prefix + texts[i] for i in idx])
            with self._lock:
                for i, v in zip(idx, vecs):
                    out[i] = v
                    self._db.execute("insert or replace into c values (?,?)",
                                     (self._key(self.ep.document_prefix + texts[i]), np.asarray(v, dtype="float32").tobytes()))
                self._db.commit()
            if progress:
                progress(min(s + self.ep.batch_size, len(todo)), len(todo))
        return out

    def embed_query(self, text, timeout=None):
        return self._call([self.ep.query_prefix + text], timeout or self.ep.query_timeout)[0]


def _norm(v):
    s = sum(x * x for x in v) ** 0.5 or 1.0
    return [x / s for x in v]


class Reranker:
    def __init__(self, ep: Endpoint):
        self.ep = ep
        self._local = None

    @property
    def enabled(self):
        return self.ep.enabled

    def rerank(self, query, docs):
        """Return a relevance score per doc (same order)."""
        if not docs or not self.enabled:
            return None
        if self.ep.provider == "local":
            if self._local is None:
                from fastembed.rerank.cross_encoder import TextCrossEncoder
                self._local = TextCrossEncoder(model_name=self.ep.model, cache_dir=os.path.join(DATA_DIR, "models"))
            return [float(x) for x in self._local.rerank(query, docs)]
        # remote: Jina/Cohere/vLLM style  POST {base}/rerank {model, query, documents}
        r = httpx.post(_url(self.ep, "/rerank"), headers=_headers(self.ep), timeout=self.ep.timeout,
                       json={"model": self.ep.model, "query": query, "documents": docs, "top_n": len(docs)})
        r.raise_for_status()
        scores = [0.0] * len(docs)
        for item in r.json().get("results", []):
            scores[item["index"]] = float(item.get("relevance_score", item.get("score", 0.0)))
        return scores


class LLM:
    def __init__(self, ep: Endpoint):
        self.ep = ep

    @property
    def enabled(self):
        return self.ep.enabled

    def answer(self, question, passages, max_tokens=None, timeout=None):
        ctx = "\n\n".join(f"[{i + 1}] ({p['title']}) {p['text']}" for i, p in enumerate(passages))
        system = ("You answer questions using ONLY the numbered context passages. "
                  "Cite passages like [1]. If the context does not contain the answer, say so plainly. "
                  "Be concise. " + (self.ep.extra_prompt or ""))
        r = httpx.post(_url(self.ep, "/chat/completions"), headers=_headers(self.ep), timeout=timeout or self.ep.timeout,
                       json={"model": self.ep.model, "temperature": 0.1, "max_tokens": max_tokens or self.ep.max_tokens,
                             **({"reasoning_effort": self.ep.reasoning_effort} if self.ep.reasoning_effort else {}),
                             "messages": [{"role": "system", "content": system},
                                          {"role": "user", "content": f"Context:\n{ctx}\n\nQuestion: {question}"}]})
        r.raise_for_status()
        text = r.json()["choices"][0]["message"]["content"] or ""
        import re
        return re.sub(r"<think>.*?</think>\s*", "", text, flags=re.S).strip()
