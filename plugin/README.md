# Knowledge Base plugin for Paperclip

Adds **Knowledge** to the Paperclip sidebar: a management page for the [Knowledge Base service](https://github.com/senadaruc/paperclip-knowledge-base), the self-hosted RAG layer your Paperclip agents search through MCP.

| Tab | What it does |
|---|---|
| Collections | Passages, last index time and status per collection, Re-index |
| Upload | Drop PDF, Word, Excel, Markdown or text files into a collection; delete them |
| Models | Where the embedding, rerank and answer models run (CPU, GPU server, hosted), with presets and a connection test |
| Test search | Exactly what agents get back for a query |
| Access | Which connection key can search which collections |

This plugin is only the management page. The search itself runs in the Knowledge Base service; set that up first with the [quick start](https://github.com/senadaruc/paperclip-knowledge-base#quick-start).

## Install

```bash
npx paperclipai plugin install paperclip-plugin-knowledge-base
```

Then create a company secret holding the service's `RAG_ADMIN_KEY`, open the plugin's settings, pick that secret for **adminKeyRef** and check **ragUrl** (default `http://knowledge-base:8790`). The worker resolves the key from Paperclip's secret store on each call, so the browser never sees it.

## Capabilities

`ui.page.register`, `ui.sidebar.register`, `http.outbound` (calls the service's admin API at `ragUrl`), `secrets.read-ref` (resolves the admin key).

## License

MIT
