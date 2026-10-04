"""Configuration: config.yaml (structure, models, collections) + environment (keys).

Every model endpoint is OpenAI-compatible, so "local on the CPU" (the bundled Ollama
container), "a GPU server" (vLLM, GPUStack, ...) and "a hosted API" differ only in
base_url / api_key / model.
"""
import os
import re
from dataclasses import dataclass, field

import yaml

CONFIG_PATH = os.environ.get("RAG_CONFIG", "/config/config.yaml")
DATA_DIR = os.environ.get("RAG_DATA", "/data")


def _env(v):
    """Expand ${VAR} and ${VAR:-default} inside config strings."""
    if not isinstance(v, str):
        return v
    return re.sub(r"\$\{(\w+)(?::-([^}]*))?\}", lambda m: os.environ.get(m.group(1), m.group(2) or ""), v)


def _expand(o):
    if isinstance(o, dict):
        return {k: _expand(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_expand(v) for v in o]
    return _env(o)


@dataclass
class Endpoint:
    provider: str = "none"          # openai | local (rerank only) | none
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    query_prefix: str = ""          # embedding: instruction prepended to queries (Qwen3-Embedding wants one)
    document_prefix: str = ""
    batch_size: int = 16
    timeout: float = 120.0
    query_timeout: float = 10.0     # embedding: per search; past it, search goes keyword-only instead of hanging
    extra_prompt: str = ""          # llm: appended to the system prompt (e.g. "/no_think" for Qwen3)
    max_tokens: int = 700
    reasoning_effort: str = ""      # llm: e.g. "none" turns off Qwen3 thinking on Ollama; empty = do not send
    candidates: int = 12            # rerank: how many fused candidates get rescored
    max_chars: int = 600            # rerank: passage text sent to the cross-encoder

    @property
    def enabled(self):
        return self.provider not in ("", "none", None)


@dataclass
class Collection:
    name: str
    description: str
    sources: list
    anonymise: bool = False


@dataclass
class Client:
    name: str
    key: str
    collections: list            # names, or ["*"]
    allow_ask: bool = True

    def allowed(self, all_names):
        return list(all_names) if "*" in self.collections else [c for c in self.collections if c in all_names]


@dataclass
class Settings:
    embedding: Endpoint
    rerank: Endpoint
    llm: Endpoint
    collections: dict
    clients: list
    admin_key: str
    chunk_chars: int = 1400
    chunk_overlap: int = 150
    reindex_interval_minutes: int = 360
    candidates: int = 30
    customer_names: list = field(default_factory=list)


OVERRIDES = os.path.join(DATA_DIR, "settings.json")
ROLES = ("embedding", "rerank", "llm")


def load_overrides():
    import json
    try:
        return json.load(open(OVERRIDES, encoding="utf-8"))
    except Exception:
        return {}


def save_overrides(o):
    import json
    tmp = OVERRIDES + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(o, f, indent=1)
    os.chmod(tmp, 0o600)
    os.replace(tmp, OVERRIDES)


def load() -> Settings:
    raw = _expand(yaml.safe_load(open(CONFIG_PATH, encoding="utf-8")))
    # model settings saved from the admin page win over config.yaml / .env
    for role, vals in load_overrides().items():
        if role in ROLES and isinstance(vals, dict):
            raw.setdefault(role, {}).update({k: v for k, v in vals.items() if k in Endpoint.__dataclass_fields__})
    ep = lambda k: Endpoint(**{kk: vv for kk, vv in (raw.get(k) or {}).items() if kk in Endpoint.__dataclass_fields__})
    cols = {n: Collection(name=n, description=c.get("description", ""), sources=c.get("sources", []),
                          anonymise=bool(c.get("anonymise", False)))
            for n, c in (raw.get("collections") or {}).items()}
    clients = []
    for n, c in (raw.get("clients") or {}).items():
        key = c.get("key", "")
        if key:
            clients.append(Client(name=n, key=key, collections=c.get("collections", []), allow_ask=c.get("allow_ask", True)))
    s = raw.get("search") or {}
    return Settings(embedding=ep("embedding"), rerank=ep("rerank"), llm=ep("llm"), collections=cols, clients=clients,
                    admin_key=raw.get("admin_key", ""), chunk_chars=int(s.get("chunk_chars", 1400)),
                    chunk_overlap=int(s.get("chunk_overlap", 150)),
                    reindex_interval_minutes=int(raw.get("reindex_interval_minutes", 360)),
                    candidates=int(s.get("candidates", 30)), customer_names=raw.get("customer_names", []))
