import dataclasses

import pytest

from app import providers, store
from app.config import Collection, Endpoint, Settings

from conftest import REAL_CALL, write_jsonl


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    d = tmp_path / "data"
    d.mkdir()
    monkeypatch.setattr(store, "DATA_DIR", str(d))
    monkeypatch.setattr(store, "STATE", str(d / "state.json"))
    monkeypatch.setattr(providers, "DATA_DIR", str(d))
    return d


@pytest.fixture
def make_store(tmp_path, data_dir):
    qa = tmp_path / "qa.jsonl"
    write_jsonl(qa, [
        {"question": "Can the product run fully on-premises without internet access?",
         "answer": "Yes. Every component, including the models, runs inside the customer network."},
        {"question": "Is single sign-on with SAML supported?", "answer": "Yes, SAML 2.0 and OpenID Connect."},
    ])

    def make(model="fake-a", customer_names=()):
        s = Settings(embedding=Endpoint(provider="openai", base_url="http://embed.invalid/v1", model=model),
                     rerank=Endpoint(), llm=Endpoint(), clients=[], admin_key="",
                     collections={"qa": Collection("qa", "Past answers", [{"type": "qa_jsonl", "path": str(qa)}], anonymise=True)},
                     customer_names=list(customer_names))
        return store.Store(s)
    return make


def test_hybrid_search_finds_paraphrase(make_store):
    st = make_store()
    assert st.index("qa")["status"] == "indexed"
    res = st.search("on-premises without internet", ["qa"], top_k=1)
    assert res["mode"] == "hybrid"
    assert "on-premises" in res["results"][0]["text"]


def test_unchanged_sources_are_not_reindexed(make_store):
    st = make_store()
    st.index("qa")
    assert st.index("qa")["status"] == "unchanged"


def test_model_change_searches_keyword_only_until_reembedded(make_store):
    st = make_store("fake-a")
    st.index("qa")
    st.reload(dataclasses.replace(st.s, embedding=Endpoint(provider="openai", base_url="http://embed.invalid/v1", model="fake-b")))
    # the table still holds 16-dim vectors, the new model makes 24-dim query vectors
    res = st.search("SAML single sign-on", ["qa"], top_k=1)
    assert res["mode"].startswith("keyword-only")
    assert "SAML" in res["results"][0]["text"]
    assert st.info(["qa"])[0]["status"] == "changes pending"
    st.index_all()
    assert st.search("SAML single sign-on", ["qa"], top_k=1)["mode"] == "hybrid"


def test_changing_customer_names_triggers_reindex(make_store):
    st = make_store(customer_names=[r"\bACME\b"])
    st.index("qa")
    fp = st._fp("qa")
    st.reload(dataclasses.replace(st.s, customer_names=[r"\bACME\b", r"\bGlobex\b"]))
    assert st._fp("qa") != fp


def test_background_index_failure_is_reported(make_store, monkeypatch):
    st = make_store()

    def down(self, texts, timeout=None):
        raise ConnectionError("embedding server down")
    monkeypatch.setattr(providers.Embedder, "_call", down)
    res = st.index_safe("qa")
    assert res["status"] == "error"
    assert st.status["qa"].startswith("error: embedding server down")


def test_unreachable_embedder_falls_back_to_keyword_only(make_store, monkeypatch):
    st = make_store()
    st.index("qa")
    monkeypatch.setattr(providers.Embedder, "_call", lambda self, texts, timeout=None: (_ for _ in ()).throw(TimeoutError("timed out")))
    res = st.search("SAML", ["qa"], top_k=1)
    assert res["mode"] == "keyword-only (embedding model unreachable)"
    assert res["results"]


def test_corrupt_state_file_does_not_stop_start(make_store, data_dir):
    (data_dir / "state.json").write_text('{"qa": {"fingerp')
    st = make_store()
    assert st.state == {}
    assert st.index("qa")["status"] == "indexed"


def test_query_embedding_uses_short_timeout(monkeypatch, data_dir):
    seen = {}

    class R:
        def raise_for_status(self):
            pass

        def json(self):
            return {"data": [{"index": 0, "embedding": [1.0, 0.0]}]}

    def post(url, **kw):
        seen["timeout"] = kw["timeout"]
        return R()
    monkeypatch.setattr(providers.Embedder, "_call", REAL_CALL)   # the real client over a fake HTTP layer
    monkeypatch.setattr(providers.httpx, "post", post)
    e = providers.Embedder(Endpoint(provider="openai", base_url="http://x/v1", model="m", timeout=300))
    e.embed_query("hello")
    assert seen["timeout"] == 10.0
