import json

from app.config import Collection, Endpoint, Settings
from app.ingest import build_chunks, split_markdown

from conftest import write_jsonl


def settings(**kw):
    return Settings(embedding=Endpoint(), rerank=Endpoint(), llm=Endpoint(), collections={}, clients=[],
                    admin_key="", **kw)


def returned_fields(ch):
    return [ch["text"], ch["title"], ch["section"], ch["source"], ch["page"], ch["meta"]]


def test_anonymise_scrubs_every_field_agents_see(tmp_path):
    qa = tmp_path / "qa.jsonl"
    write_jsonl(qa, [{"question": "Do you support SSO for ACME?", "answer": "Yes, ACME Corp uses SAML.",
                      "source": "ACME Corp RFP", "section": "ACME security", "year": 2026}])
    col = Collection("qa", "", [{"type": "qa_jsonl", "path": str(qa)}], anonymise=True)
    [ch] = build_chunks(col, settings(customer_names=[r"\bACME(?: Corp)?\b"]))
    for value in returned_fields(ch):
        assert "ACME" not in value
    assert json.loads(ch["meta"])["source"] == "the customer RFP"
    assert json.loads(ch["meta"])["year"] == 2026   # non-string metadata is kept as is


def test_anonymise_scrubs_uploaded_file_names(tmp_path):
    (tmp_path / "ACME questionnaire.md").write_text("# Answers\n\nWe encrypt data at rest with AES-256 for every tenant.\n")
    col = Collection("docs", "", [{"type": "files_dir", "path": str(tmp_path), "label": "inbox"}], anonymise=True)
    [ch] = build_chunks(col, settings(customer_names=[r"\bACME\b"]))
    assert ch["source"] == "inbox/the customer questionnaire.md"
    assert ch["title"] == "the customer questionnaire"


def test_collection_without_anonymise_keeps_names(tmp_path):
    (tmp_path / "ACME questionnaire.md").write_text("# Answers\n\nACME asked whether data is encrypted at rest.\n")
    col = Collection("private", "", [{"type": "files_dir", "path": str(tmp_path)}])
    [ch] = build_chunks(col, settings(customer_names=[r"\bACME\b"]))
    assert "ACME" in ch["text"] and "ACME" in ch["source"]


def test_split_markdown_keeps_headings_and_size():
    text = "# Title\n\nintro\n\n## Install\n\n" + "\n\n".join(f"paragraph {i} " + "x" * 80 for i in range(20))
    out = split_markdown(text, max_chars=300, overlap=50)
    assert {head for head, _ in out} == {"Title", "Install"}
    assert all(len(body) <= 300 + 50 + 2 for _, body in out)


def test_split_markdown_hard_splits_long_paragraph():
    out = split_markdown("y" * 1000, max_chars=300, overlap=0)
    assert len(out) == 4 and all(len(body) <= 300 for _, body in out)
