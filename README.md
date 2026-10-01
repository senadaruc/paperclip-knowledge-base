# Knowledge Base for Paperclip

**Give your Paperclip agents a searchable company memory — self-hosted, permission-aware, and managed from inside Paperclip.**

Agents answer better when they can look things up: product documentation, manuals, past answers to customer questionnaires, internal notes. This project adds a retrieval (RAG) layer that Paperclip agents reach as an ordinary MCP tool, plus a Paperclip plugin that puts its management page in the Paperclip sidebar.

It answers the needs raised in [#1858 — company-level knowledge layer](https://github.com/paperclipai/paperclip/issues/1858) and [#7545 — company-wide knowledge base](https://github.com/paperclipai/paperclip/issues/7545), without touching Paperclip core: it is built only on the plugin system and the existing MCP tool connections.

```mermaid
flowchart LR
  subgraph Paperclip
    A1[Support agent] -- staff key --> C1[MCP connection]
    A2[Leadership agent] -- leadership key --> C2[MCP connection]
    P[Knowledge Base plugin<br/>sidebar page]
  end
  C1 --> S
  C2 --> S
  P -- admin key from Paperclip secrets --> S
  subgraph S[knowledge-base service]
    T[MCP tools: search, ask, list_collections]
    I[Indexer: md, pdf, docx, xlsx, Q&A jsonl]
    L[(LanceDB<br/>vectors + full text)]
  end
  S -- OpenAI-compatible APIs --> M[Embedding / rerank / LLM<br/>CPU (Ollama), GPU server, or hosted]
```

## What you get

- **Hybrid search that understands paraphrases.** Vector search and full-text search run side by side and are merged with reciprocal rank fusion, then a cross-encoder reranks the best candidates. Exact product names still match; "account hijacked" still finds "compromised accounts".
- **Collections with access control.** Each Paperclip connection gets its own key, and the key decides which collections it can search. A staff-facing agent can never reach the leadership collection, even if asked to.
- **Models wherever you want them.** Embedding, reranking and the optional answer model are plain OpenAI-compatible endpoints: the bundled Ollama on CPU, your own GPU server (vLLM, GPUStack, …) or a hosted API. Switch from the UI; the service re-embeds automatically when the embedding model changes. If the embedding endpoint is down, search degrades to keyword-only instead of failing.
- **Drop-in ingestion.** Markdown wikis (with page links), folders of PDF / Word / Excel / Markdown files, and a Q&A library in JSON Lines. Changed sources are re-indexed on a schedule; finished embeddings are cached, so only new text is embedded again.
- **Optional anonymisation.** Collections marked `anonymise: true` replace configured customer names before indexing — useful when past answers to customer questionnaires should be reusable by everyone.
- **A management page inside Paperclip.** The plugin adds **Knowledge** to the sidebar: collection status and re-index, drag-and-drop upload and delete, model settings with presets and a connection test, a test search that shows exactly what agents get, and the access map.

<details>
<summary>Screens of the plugin page (tabs)</summary>

| Tab | What it does |
|---|---|
| Collections | Passages, last index time and status per collection (`ready`, `embedding 120/480`, `changes pending`, `queued`), Re-index button |
| Upload | Pick a collection, drop files, see and delete uploaded files; indexing starts immediately |
| Models | Embedding, reranker and answer model: provider, base URL, model, API key; presets for CPU, GPU server and hosted; Save and Test |
| Test search | Query one or all collections and see the passages, sources and scores agents receive |
| Access | Which connection key can search which collections |

</details>

## Quick start

### 1. Run the service (next to Paperclip)

```bash
git clone https://github.com/senadaruc/paperclip-knowledge-base
cd paperclip-knowledge-base/service
cp .env.example .env                                   # fill in three random keys
cp config/config.example.yaml config/config.yaml       # adjust collections and clients
mkdir -p data inbox/docs inbox/qa inbox/private sources/wiki ollama
docker compose up -d --build                           # joins the paperclip_default network
docker exec kb-ollama ollama pull qwen3-embedding:0.6b
docker exec kb-ollama ollama pull qwen3:1.7b           # only for the optional `ask` tool
```

Open `http://127.0.0.1:8790/admin/ui`, sign in with `RAG_ADMIN_KEY`, and drop a few files into a collection. Copy the examples to try it quickly: `cp ../examples/qa.example.jsonl sources/qa.jsonl && cp -r ../examples/wiki/* sources/wiki/`.

If Paperclip runs on another Docker network, set `PAPERCLIP_NETWORK` in `.env`. If it does not run in Docker, publish port 8790 behind your reverse proxy and use that URL below.

### 2. Connect agents (no code — Paperclip UI)

In **Connectors**, add a remote MCP server by link, once per access level. Use the service URL and paste the client key as the app key (Paperclip sends it as a Bearer token):

| Connection | URL | App key | Install for |
|---|---|---|---|
| Knowledge Base (staff) | `http://knowledge-base:8790/mcp` | `RAG_KEY_STAFF` | the agents that talk to staff or customers |
| Knowledge Base (leadership) | `http://knowledge-base:8790/mcp` | `RAG_KEY_LEADERSHIP` | leadership agents only |

Choose **Just agents I pick** for each connection, so no other agent gets the tools. The agents now have three tools: `search`, `list_collections` and `ask`.

Tell the agent when to use them, for example in its instructions or a skill:

> For questions about our product, documentation or past customer answers, call the Knowledge Base `search` tool first (collections `wiki`, `docs`, `qa`). Read the full article when a result points to one. Cite the source. If nothing relevant comes back, say so.

### 3. Install the plugin (management page in Paperclip)

```bash
cd ../plugin
npm install && npm run build
npx paperclipai plugin install "$(pwd)"
```

Then create a company secret holding `RAG_ADMIN_KEY`, open the plugin's settings form (`/settings/plugins/<pluginId>`), pick that secret for **adminKeyRef** (a secret picker, the key never appears in plain text) and check **ragUrl** (default `http://knowledge-base:8790`). The **Knowledge** entry appears in the sidebar.

The plugin worker resolves the admin key from Paperclip's secret store at call time, so the browser never sees it, and access to the page follows Paperclip's own sign-in.

## Collections and sources

```yaml
collections:
  wiki:
    description: Product wiki with page links
    sources:
      - {type: wiki_dir, path: /sources/wiki}            # <area>/<slug>.md, optional line: Page: `/path`
  qa:
    description: Past answers to customer questions
    anonymise: true
    sources:
      - {type: qa_jsonl, path: /sources/qa.jsonl}         # {requirement|question, answer, compliance?, notes?, source?, year?}
      - {type: files_dir, path: /inbox/qa}                # answered questionnaires dropped later
  private:
    description: Leadership-only material
    sources:
      - {type: files_dir, path: /inbox/private}
```

| Source type | Reads | Notes |
|---|---|---|
| `wiki_dir` | `.md` files | Title from the first heading, page link from a `Page: \`/path\`` line, split by headings |
| `markdown_dir` | `.md`, `.txt` | Same chunking, no page links |
| `files_dir` | `.md .txt .pdf .docx .xlsx` | The upload target for the UI; Word tables and Excel rows keep their column names |
| `qa_jsonl` | JSON Lines | One past answer per line; newer years win when agents compare answers |

## Where the models run

| Role | Default (CPU) | GPU server | Hosted |
|---|---|---|---|
| Embedding | `qwen3-embedding:0.6b` via the bundled Ollama | e.g. `Qwen/Qwen3-Embedding-4B` on vLLM | any OpenAI-compatible `/embeddings` |
| Rerank | `jinaai/jina-reranker-v1-turbo-en` inside the service (ONNX) | e.g. `Qwen/Qwen3-Reranker-4B` via `/rerank` | Jina- or Cohere-compatible `/rerank` |
| Answer model (`ask` only) | `qwen3:1.7b` via Ollama | any chat model | any `/chat/completions` |

Change them on the Models tab (saved to `data/settings.json`, applied without a restart) or with the `EMBED_*`, `RERANK_*`, `LLM_*` variables in `.env`.

### Measured on a small server

On a low-power 4-core CPU (AMD Ryzen Embedded V1500B, no GPU), with the CPU defaults:

| | Result |
|---|---|
| First-time embedding | about 25 passages per minute (cached afterwards) |
| Search, warm | 1–3 s including rerank |
| Paraphrase recall test, 20 reworded questions over 822 past answers, target in top 5 | **17/20 hybrid** vs 14/20 keyword-only |

A GPU server turns the first-time embedding from hours into seconds and allows the larger embedding and rerank models.

## MCP tools

| Tool | Arguments | Returns |
|---|---|---|
| `list_collections` | — | The collections this key may search, with description, passage count, status |
| `search` | `query`, `collection` (optional, comma-separated), `top_k` (1–20) | Passages with title, section, page link, source, score and search mode |
| `ask` | `question`, `collection` (optional) | A short answer from the configured LLM with numbered citations |

Admin endpoints (admin key or the admin page's session cookie): `GET /admin/api/overview`, `POST /admin/api/settings`, `POST /admin/api/test`, `POST /admin/api/upload`, `POST /admin/api/delete`, `POST /admin/api/search`, `POST /admin/reindex?collection=&force=1`, `GET /admin/status`. `GET /health` is open.

## Security notes

- Every MCP request needs a client key; collection access is enforced by the service, not by prompts.
- Keep the service on the same private Docker network as Paperclip. If you publish the admin page, put it behind TLS and your SSO or VPN.
- The plugin keeps the admin key in Paperclip's secret store and resolves it in the worker.
- Uploads are limited to known file types and 50 MB; delete paths are confined to the collection's upload folder.
- Agents that run inside the Paperclip container can read files in that container. Do not put the service's `.env` there.

## Limitations and ideas

- One process indexes one collection at a time; a large first index on CPU takes hours.
- Changing `chunk_chars` or the embedding model re-embeds everything.
- Scanned PDFs need OCR before upload.
- Possible next steps: per-collection embedding models, incremental per-file indexing, OCR, and a native Paperclip skill that teaches agents when to search.

## Project layout

```
service/   Python MCP server, indexer, admin page (Docker)
plugin/    Paperclip plugin: sidebar entry + management page (React) + worker
examples/  A tiny wiki and Q&A library to try it
```

## License

MIT © 2026 Senad Aruc
