"""Test setup: a throwaway data dir and config, and a fake embedding model so no test needs a network.

app.config reads RAG_DATA / RAG_CONFIG at import time, so they are set here, before any app module loads.
"""
import hashlib
import json
import os
import re
import sys
import tempfile

import pytest

ROOT = tempfile.mkdtemp(prefix="kb-test-")
os.environ["RAG_DATA"] = os.path.join(ROOT, "data")
os.environ["RAG_CONFIG"] = os.path.join(ROOT, "config.yaml")
os.makedirs(os.environ["RAG_DATA"])
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

WIKI = os.path.join(ROOT, "sources", "wiki", "security")
os.makedirs(WIKI)
with open(os.path.join(WIKI, "passwords.md"), "w") as f:
    f.write("# Password reset\n\nPage: `/settings/security`\n\n## Steps\n\n"
            "Open Settings, then Security, and choose Reset password. A reset link is sent by email.\n")
with open(os.path.join(ROOT, "config.yaml"), "w") as f:
    f.write(f"""
embedding: {{provider: openai, base_url: http://embed.invalid/v1, model: fake-a}}
rerank: {{provider: none}}
llm: {{provider: none}}
admin_key: admin-secret
collections:
  wiki:
    description: Product wiki
    sources: [{{type: wiki_dir, path: {os.path.join(ROOT, "sources", "wiki")}}}]
  private:
    description: Leadership only
    sources: [{{type: files_dir, path: {os.path.join(ROOT, "inbox", "private")}}}]
clients:
  staff: {{key: staff-key, collections: [wiki]}}
  leadership: {{key: lead-key, collections: ["*"]}}
""")

DIMS = {"fake-a": 16, "fake-b": 24}


def fake_vectors(model, texts):
    """Bag-of-words hashed into a small vector; the size depends on the model, like real models."""
    dim = DIMS[model]
    out = []
    for t in texts:
        v = [0.0] * dim
        for w in re.findall(r"\w+", t.lower()):
            v[int(hashlib.md5(w.encode()).hexdigest(), 16) % dim] += 1.0
        s = sum(x * x for x in v) ** 0.5 or 1.0
        out.append([x / s for x in v])
    return out


from app import providers  # noqa: E402  (after the environment above)

REAL_CALL = providers.Embedder._call


@pytest.fixture(autouse=True)
def fake_embedder(monkeypatch):
    monkeypatch.setattr(providers.Embedder, "_call", lambda self, texts, timeout=None: fake_vectors(self.ep.model, texts))


def write_jsonl(path, rows):
    with open(path, "w") as f:
        f.write("\n".join(json.dumps(r) for r in rows) + "\n")
