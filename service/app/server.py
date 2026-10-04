"""MCP server (streamable HTTP at /mcp) + admin endpoints.

Every MCP request must carry `Authorization: Bearer <client key>`; the key decides which
collections that client may search. Admin endpoints use the separate admin key.
"""
import contextvars
import hmac
import json
import logging
import os
import threading
import time

import uvicorn
from mcp.server.fastmcp import FastMCP
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse

from . import config
from .store import Store

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("rag.server")

S = config.load()
STORE = Store(S)
CLIENT = contextvars.ContextVar("rag_client", default=None)

try:
    from mcp.server.transport_security import TransportSecuritySettings
    _sec = {"transport_security": TransportSecuritySettings(enable_dns_rebinding_protection=False)}
except Exception:  # older SDK
    _sec = {}

mcp = FastMCP("knowledge-base", stateless_http=True, json_response=True,
              instructions=("Search the company knowledge base (product docs, manuals, past answers and any other configured collections). "
                            "Call list_collections first if unsure what is available, then search with a focused query. "
                            "Results carry the source and, for Wiki pages, the product page path."), **_sec)


def _client():
    c = CLIENT.get()
    if c is None:
        raise PermissionError("unauthenticated")
    return c


def _pick(collection):
    c = _client()
    allowed = c.allowed(S.collections.keys())
    if not collection:
        return allowed
    wanted = [x.strip() for x in collection.split(",") if x.strip()]
    denied = [x for x in wanted if x not in allowed]
    if denied:
        raise PermissionError(f"collection not available to this client: {', '.join(denied)}")
    return wanted


@mcp.tool()
async def list_collections() -> str:
    """List the knowledge collections you can search, with what each contains and how many passages it holds."""
    c = _client()
    return json.dumps(await run_in_threadpool(STORE.info, c.allowed(S.collections.keys())), ensure_ascii=False, indent=1)


@mcp.tool()
async def search(query: str, collection: str = "", top_k: int = 8) -> str:
    """Semantic + keyword search over the company knowledge base.

    query: a natural-language question or a requirement text (English works best).
    collection: optional, one name or a comma-separated list from list_collections; empty searches all you may use.
    top_k: number of passages to return (1-20).
    Returns passages with title, section, product page path (Wiki), source and score.
    """
    top_k = max(1, min(int(top_k or 8), 20))
    cols = _pick(collection)
    return json.dumps(await run_in_threadpool(STORE.search, query, cols, top_k), ensure_ascii=False, indent=1)


@mcp.tool()
async def ask(question: str, collection: str = "") -> str:
    """Answer a question with the knowledge base's own model (local or hosted) and cite sources.
    Prefer `search` when you can reason over the passages yourself; use `ask` for a quick cited summary."""
    c = _client()
    if not c.allow_ask:
        return json.dumps({"error": "ask is disabled for this client; use search"})
    cols = _pick(collection)
    return json.dumps(await run_in_threadpool(STORE.ask, question, cols), ensure_ascii=False, indent=1)


@mcp.custom_route("/health", methods=["GET"])
async def health(_: Request):
    return JSONResponse({"ok": True, "collections": {k: v.get("chunks", 0) for k, v in STORE.state.items()},
                         "embedding": S.embedding.model, "embedding_base": S.embedding.base_url,
                         "rerank": f"{S.rerank.provider}:{S.rerank.model}" if S.rerank.enabled else "none",
                         "llm": S.llm.model if S.llm.enabled else "none"})


@mcp.custom_route("/admin/status", methods=["GET"])
async def admin_status(_: Request):
    return JSONResponse({"state": STORE.state, "status": STORE.status})


@mcp.custom_route("/admin/reindex", methods=["POST"])
async def admin_reindex(req: Request):
    name, force = req.query_params.get("collection"), req.query_params.get("force") == "1"
    if name and name not in S.collections:
        return JSONResponse({"error": f"unknown collection: {name}"}, 404)
    def run():
        STORE.index_safe(name, force) if name else STORE.index_all(force)
    threading.Thread(target=run, daemon=True).start()
    return JSONResponse({"started": name or "all", "force": force})


# ------------------------------------------------------------------ admin page + API
import hashlib
import re as _re

from .ingest import FILE_TYPES, source_files

UI_HTML = open(os.path.join(os.path.dirname(__file__), "ui.html"), encoding="utf-8").read()
MAX_UPLOAD = 50 * 1024 * 1024


def _cookie_value():
    return hmac.new(S.admin_key.encode(), b"paperclip-rag-ui", hashlib.sha256).hexdigest() if S.admin_key else ""


def _inbox(name):
    """The writable drop folder of a collection (its files_dir source under /inbox), or None."""
    for src in S.collections[name].sources:
        if src["type"] == "files_dir" and src["path"].startswith("/inbox"):
            return src["path"]
    return None


def _mask(ep):
    d = {k: getattr(ep, k) for k in ("provider", "base_url", "model", "query_prefix", "candidates", "max_chars", "batch_size", "timeout")}
    d["api_key_set"] = bool(ep.api_key)
    return d


def _bg(fn, *a):
    threading.Thread(target=fn, args=a, daemon=True).start()


@mcp.custom_route("/", methods=["GET"])
async def root(_: Request):
    return RedirectResponse("/admin/ui")


@mcp.custom_route("/admin/ui", methods=["GET"])
async def admin_ui(_: Request):
    return HTMLResponse(UI_HTML)


@mcp.custom_route("/admin/login", methods=["POST"])
async def admin_login(req: Request):
    body = await req.json()
    if not (S.admin_key and hmac.compare_digest(str(body.get("key", "")), S.admin_key)):
        return JSONResponse({"error": "wrong admin key"}, 401)
    r = JSONResponse({"ok": True})
    r.set_cookie("rag_admin", _cookie_value(), httponly=True, secure=True, samesite="strict", max_age=30 * 86400, path="/admin")
    return r


@mcp.custom_route("/admin/logout", methods=["POST"])
async def admin_logout(_: Request):
    r = JSONResponse({"ok": True})
    r.delete_cookie("rag_admin", path="/admin")
    return r


@mcp.custom_route("/admin/api/overview", methods=["GET"])
async def admin_overview(_: Request):
    cols = []
    for info in await run_in_threadpool(STORE.info, list(S.collections)):
        n = info["name"]
        box = _inbox(n)
        files = []
        if box and os.path.isdir(box):
            for f in source_files({"type": "files_dir", "path": box}):
                st = os.stat(f)
                files.append({"name": os.path.relpath(f, box), "size": st.st_size, "modified": int(st.st_mtime)})
        others = [f"{src['type']}: {src['path']}" for src in S.collections[n].sources if not (src["type"] == "files_dir" and src["path"] == box)]
        cols.append({**info, "uploads": box is not None, "files": files, "other_sources": others,
                     "anonymise": S.collections[n].anonymise})
    return JSONResponse({"collections": cols,
                         "models": {r: _mask(getattr(S, r)) for r in config.ROLES},
                         "clients": [{"name": c.name, "collections": c.allowed(S.collections.keys())} for c in S.clients],
                         "reindex_interval_minutes": S.reindex_interval_minutes,
                         "file_types": list(FILE_TYPES)})


@mcp.custom_route("/admin/api/settings", methods=["POST"])
async def admin_settings(req: Request):
    global S
    body = await req.json()
    role = body.get("role")
    if role not in config.ROLES:
        return JSONResponse({"error": "unknown role"}, 400)
    ov = config.load_overrides()
    cur = dict(ov.get(role, {}))
    for k in ("provider", "base_url", "model", "query_prefix"):
        if k in body:
            cur[k] = str(body[k]).strip()
    for k in ("candidates", "max_chars", "batch_size"):
        if body.get(k) not in (None, ""):
            cur[k] = int(body[k])
    if body.get("api_key"):
        cur["api_key"] = str(body["api_key"]).strip()
    if body.get("clear_api_key"):
        cur["api_key"] = ""
    ov[role] = cur
    config.save_overrides(ov)
    old_embed = STORE.embedder.id
    S = await run_in_threadpool(config.load)
    await run_in_threadpool(STORE.reload, S)
    reembed = role == "embedding" and STORE.embedder.id != old_embed
    if reembed:
        _bg(STORE.index_all)
    return JSONResponse({"ok": True, "reembedding": reembed, "models": {r: _mask(getattr(S, r)) for r in config.ROLES}})


@mcp.custom_route("/admin/api/test", methods=["POST"])
async def admin_test(req: Request):
    role = (await req.json()).get("role")
    LIMIT = 25  # Paperclip's plugin bridge gives up after 30 s

    def run():
        if role == "embedding":
            v = STORE.embedder.embed_query("connection test", timeout=LIMIT)
            return f"{len(v)}-dimension vector"
        if role == "rerank":
            if not STORE.reranker.enabled:
                return "reranking is off"
            sc = STORE.reranker.rerank("how do I reset my password", ["Open Settings, then Security, and choose Reset password.", "The weather is nice."])
            return f"scores {[round(x, 2) for x in sc]}"
        if role == "llm":
            if not STORE.llm.enabled:
                return "LLM is off"
            return STORE.llm.answer("Reply with the single word OK.", [{"title": "test", "text": "OK"}], max_tokens=60, timeout=LIMIT)[:120] or "responded with an empty answer"
        raise ValueError("unknown role")

    t0 = time.time()
    try:
        detail = await run_in_threadpool(run)
        return JSONResponse({"ok": True, "detail": detail, "ms": int((time.time() - t0) * 1000)})
    except Exception as e:
        msg = str(e)[:300]
        if "timed out" in msg.lower() or "timeout" in type(e).__name__.lower():
            busy = any(str(v).startswith(("embedding", "chunking")) for v in STORE.status.values())
            msg = (f"no answer within {LIMIT} s" + (" — the model is busy indexing documents right now; try again when indexing is done" if busy else ""))
        return JSONResponse({"ok": False, "detail": msg, "ms": int((time.time() - t0) * 1000)})


@mcp.custom_route("/admin/api/upload", methods=["POST"])
async def admin_upload(req: Request):
    form = await req.form(max_files=50, max_part_size=MAX_UPLOAD)
    name = str(form.get("collection", ""))
    box = _inbox(name) if name in S.collections else None
    if not box:
        return JSONResponse({"error": "this collection does not take uploads"}, 400)
    os.makedirs(box, exist_ok=True)
    saved, rejected = [], []
    for up in form.getlist("files"):
        fn = _re.sub(r"[^\w.\- ()&]+", "_", os.path.basename(up.filename or "")).strip()
        data = await up.read()
        if not fn or not fn.lower().endswith(FILE_TYPES):
            rejected.append(f"{up.filename}: unsupported type"); continue
        if len(data) > MAX_UPLOAD:
            rejected.append(f"{fn}: larger than 50 MB"); continue
        with open(os.path.join(box, fn), "wb") as f:
            f.write(data)
        saved.append(fn)
    if saved:
        _bg(STORE.index_safe, name)
    return JSONResponse({"saved": saved, "rejected": rejected, "indexing": bool(saved)})


@mcp.custom_route("/admin/api/delete", methods=["POST"])
async def admin_delete(req: Request):
    body = await req.json()
    name, rel = body.get("collection", ""), body.get("name", "")
    box = _inbox(name) if name in S.collections else None
    path = os.path.realpath(os.path.join(box or "/nonexistent", rel))
    if not box or not path.startswith(os.path.realpath(box) + os.sep) or not os.path.isfile(path):
        return JSONResponse({"error": "file not found"}, 404)
    os.remove(path)
    _bg(STORE.index_safe, name)
    return JSONResponse({"ok": True})


@mcp.custom_route("/admin/api/search", methods=["POST"])
async def admin_search(req: Request):
    body = await req.json()
    cols = [body["collection"]] if body.get("collection") else list(S.collections)
    t0 = time.time()
    res = await run_in_threadpool(STORE.search, str(body.get("query", "")), cols, int(body.get("top_k") or 5))
    res["ms"] = int((time.time() - t0) * 1000)
    return JSONResponse(res)


def _scheduler():
    while True:
        try:
            STORE.index_all()
        except Exception:
            log.exception("scheduled index failed")
        time.sleep(max(5, S.reindex_interval_minutes) * 60)


class Auth:
    """ASGI wrapper: bearer key -> client for /mcp, admin key for /admin, /health open."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        auth = dict(scope.get("headers") or []).get(b"authorization", b"").decode()
        token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
        if path.startswith("/admin") and path not in ("/admin/ui", "/admin/login", "/admin/logout"):
            cookies = dict(scope.get("headers") or []).get(b"cookie", b"").decode()
            m = _re.search(r"(?:^|;\s*)rag_admin=([0-9a-f]{64})", cookies)
            by_cookie = bool(m and S.admin_key and hmac.compare_digest(m.group(1), _cookie_value()))
            by_key = bool(S.admin_key and token and hmac.compare_digest(token, S.admin_key))
            if not (by_cookie or by_key):
                return await JSONResponse({"error": "admin login required"}, 401)(scope, receive, send)
        elif path.startswith("/mcp"):
            client = next((c for c in S.clients if token and hmac.compare_digest(token, c.key)), None)
            if client is None:
                return await JSONResponse({"error": "invalid or missing API key"}, 401)(scope, receive, send)
            tok = CLIENT.set(client)
            try:
                return await self.app(scope, receive, send)
            finally:
                CLIENT.reset(tok)
        return await self.app(scope, receive, send)


def main():
    threading.Thread(target=_scheduler, daemon=True).start()
    app = Auth(mcp.streamable_http_app())
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8790")), log_level="warning")


if __name__ == "__main__":
    main()
