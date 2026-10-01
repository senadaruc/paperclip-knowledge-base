import { useState } from "react";
import {
  usePluginAction,
  usePluginData,
  useHostNavigation,
  usePluginToast,
} from "@paperclipai/plugin-sdk/ui";
import type { PluginPageProps, PluginSidebarProps } from "@paperclipai/plugin-sdk/ui";

type FileRow = { name: string; size: number; modified: number };
type Col = {
  name: string; description: string; chunks: number; indexed_at: string | null; status: string;
  uploads: boolean; files: FileRow[]; other_sources: string[]; anonymise: boolean;
};
type Model = { provider: string; base_url: string; model: string; api_key_set: boolean; candidates?: number };
type Overview = {
  collections: Col[]; models: Record<"embedding" | "rerank" | "llm", Model>;
  clients: { name: string; collections: string[] }[]; reindex_interval_minutes: number; file_types: string[];
};
type Hit = { collection: string; title: string; section: string; page: string; source: string; text: string; score: number };

const ROUTE = "knowledge";
const ROLES = [
  { key: "embedding", title: "Embedding", hint: "Turns passages and questions into vectors. Required." },
  { key: "rerank", title: "Reranker", hint: "Re-orders the best candidates. “local” runs on the CPU inside the service." },
  { key: "llm", title: "Answer model", hint: "Only for the ask tool; agents normally reason with Claude." },
] as const;
const PRESETS: Record<string, Record<string, Partial<Model>>> = {
  "CPU (bundled Ollama)": {
    embedding: { provider: "openai", base_url: "http://ollama:11434/v1", model: "qwen3-embedding:0.6b" },
    rerank: { provider: "local", base_url: "", model: "jinaai/jina-reranker-v1-turbo-en", candidates: 12 },
    llm: { provider: "openai", base_url: "http://ollama:11434/v1", model: "qwen3:1.7b" },
  },
  "GPU server (vLLM)": {
    embedding: { provider: "openai", base_url: "http://gpu-server:8000/v1", model: "Qwen/Qwen3-Embedding-4B" },
    rerank: { provider: "openai", base_url: "http://gpu-server:8000/v1", model: "Qwen/Qwen3-Reranker-4B", candidates: 30 },
    llm: { provider: "openai", base_url: "http://gpu-server:8000/v1", model: "openai/gpt-oss-120b" },
  },
  "Hosted API": {
    embedding: { provider: "openai", base_url: "https://api.example.com/v1", model: "" },
    rerank: { provider: "openai", base_url: "https://api.example.com/v1", model: "" },
    llm: { provider: "openai", base_url: "https://api.example.com/v1", model: "" },
  },
};

const card = "rounded-lg border border-border bg-card p-4";
const btn = "inline-flex items-center gap-1.5 whitespace-nowrap rounded-md border border-border bg-background px-3 py-1.5 text-[13px] font-medium hover:bg-accent disabled:opacity-50";
const btnPrimary = "inline-flex items-center gap-1.5 whitespace-nowrap rounded-md bg-primary px-3 py-1.5 text-[13px] font-medium text-primary-foreground hover:opacity-90 disabled:opacity-50";
const input = "w-full rounded-md border border-border bg-background px-2.5 py-1.5 text-[13px] outline-none focus:border-foreground/40";
const label = "mb-1 mt-3 block text-[11px] font-semibold uppercase tracking-wide text-muted-foreground";

function errText(e: unknown) {
  if (e && typeof e === "object" && "message" in e) return String((e as { message: unknown }).message);
  return String(e);
}
function size(b: number) { return b > 1048576 ? `${(b / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(b / 1024))} KB`; }
const BRAND = "#ee3342";
function Pill({ s }: { s: string }) {
  // inline colours: the host stylesheet only ships the Tailwind classes Paperclip itself uses
  const tone = s === "ready" ? { background: "rgba(16,185,129,.15)", color: "#059669" }
    : s.startsWith("error") ? { background: "rgba(239,68,68,.15)", color: "#dc2626" }
    : s === "not indexed" || s === "empty" ? undefined
    : { background: "rgba(245,158,11,.18)", color: "#b45309" };
  return <span title={s} style={tone} className={`inline-block rounded-full px-2 py-0.5 text-[11px] font-semibold ${tone ? "" : "bg-muted text-muted-foreground"}`}>{s.startsWith("error") ? "error" : s}</span>;
}

function BookIcon() {
  return (
    <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M4 19.5V5a2 2 0 0 1 2-2h13v16H6a2 2 0 0 0-2 2.5Z" /><path d="M8 7h7M8 11h5" />
    </svg>
  );
}

export function KnowledgeSidebarLink(_: PluginSidebarProps) {
  const nav = useHostNavigation();
  const href = nav.resolveHref(`/${ROUTE}`);
  const active = typeof window !== "undefined" && window.location.pathname === href;
  return (
    <a {...nav.linkProps(`/${ROUTE}`)} aria-current={active ? "page" : undefined}
      className={["flex items-center gap-2.5 px-3 py-2 text-[13px] font-medium transition-colors",
        active ? "bg-accent text-foreground" : "text-foreground/80 hover:bg-accent/50 hover:text-foreground"].join(" ")}>
      <BookIcon /><span>Knowledge</span>
    </a>
  );
}

export function KnowledgePage({ context }: PluginPageProps) {
  const companyId = context.companyId ?? undefined;
  const params = companyId ? { companyId } : {};
  const ov = usePluginData<Overview>("overview", params);
  const [tab, setTab] = useState<"collections" | "upload" | "models" | "search" | "access">("collections");
  const tabs = [["collections", "Collections"], ["upload", "Upload"], ["models", "Models"], ["search", "Test search"], ["access", "Access"]] as const;

  return (
    <div className="mx-auto max-w-5xl space-y-4 p-6">
      <div className="flex items-center gap-3">
        <div className="grid h-9 w-9 place-items-center rounded-lg text-white" style={{ background: BRAND }}><BookIcon /></div>
        <div className="flex-1">
          <h1 className="text-lg font-semibold">Knowledge Base</h1>
          <p className="text-[13px] text-muted-foreground">What your agents can search: documentation, files and past answers.</p>
        </div>
        <button className={btn} onClick={() => ov.refresh()}>Refresh</button>
      </div>
      <div className="flex gap-1 border-b border-border">
        {tabs.map(([k, t]) => (
          <button key={k} onClick={() => setTab(k)}
            style={{ borderBottomColor: tab === k ? BRAND : "transparent" }}
            className={`-mb-px border-b-2 px-3 py-2 text-[13px] ${tab === k ? "font-semibold text-foreground" : "text-muted-foreground hover:text-foreground"}`}>{t}</button>
        ))}
      </div>
      {ov.error && <div className={`${card} text-[13px]`} style={{ color: "#dc2626" }}>Could not reach the knowledge service: {ov.error.message}</div>}
      {!ov.data && !ov.error && <div className="text-[13px] text-muted-foreground">Loading…</div>}
      {ov.data && tab === "collections" && <Collections data={ov.data} companyId={companyId} refresh={ov.refresh} />}
      {ov.data && tab === "upload" && <Upload data={ov.data} companyId={companyId} refresh={ov.refresh} />}
      {ov.data && tab === "models" && <Models data={ov.data} companyId={companyId} refresh={ov.refresh} />}
      {ov.data && tab === "search" && <Search data={ov.data} companyId={companyId} />}
      {ov.data && tab === "access" && <Access data={ov.data} />}
    </div>
  );
}

type SectionProps = { data: Overview; companyId?: string; refresh: () => void };

function Collections({ data, companyId, refresh }: SectionProps) {
  const reindex = usePluginAction("reindex");
  const toast = usePluginToast();
  return (
    <div className={card}>
      <h2 className="text-[15px] font-semibold">Collections</h2>
      <p className="mb-3 text-[13px] text-muted-foreground">Changed sources are re-indexed every {data.reindex_interval_minutes} minutes. Re-index runs it now.</p>
      <table className="w-full text-[13px]">
        <thead><tr className="text-left text-[11px] uppercase tracking-wide text-muted-foreground">
          <th className="py-2">Collection</th><th>Passages</th><th>Status</th><th /></tr></thead>
        <tbody>
          {data.collections.map((c) => (
            <tr key={c.name} className="border-t border-border align-top">
              <td className="py-2.5 pr-3">
                <div className="font-semibold">{c.name}{c.anonymise && <span className="ml-2 rounded-full bg-muted px-2 py-0.5 text-[11px] text-muted-foreground">anonymised</span>}</div>
                <div className="text-muted-foreground">{c.description}</div>
                <div className="mt-1 text-[12px] text-muted-foreground">{c.uploads ? `${c.files.length} uploaded file(s)` : "no uploads"}{c.other_sources.length ? ` · ${c.other_sources.length} built-in source(s)` : ""}</div>
              </td>
              <td className="py-2.5">{c.chunks}<div className="text-[12px] text-muted-foreground">{c.indexed_at ?? ""}</div></td>
              <td className="py-2.5"><Pill s={c.status} /></td>
              <td className="py-2.5 text-right">
                <button className={btn} onClick={async () => {
                  try { await reindex({ companyId, collection: c.name }); toast({ title: `Re-indexing ${c.name}` }); setTimeout(refresh, 1500); }
                  catch (e) { toast({ title: "Re-index failed", body: errText(e), tone: "error" }); }
                }}>Re-index</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Upload({ data, companyId, refresh }: SectionProps) {
  const uploadable = data.collections.filter((c) => c.uploads);
  const [col, setCol] = useState(uploadable[0]?.name ?? "");
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  const [over, setOver] = useState(false);
  const upload = usePluginAction("upload");
  const del = usePluginAction("deleteFile");
  const toast = usePluginToast();
  const current = data.collections.find((c) => c.name === col);

  async function send(list: FileList | null) {
    if (!list || !list.length) return;
    const files = Array.from(list);
    const tooBig = files.filter((f) => f.size > 25 * 1024 * 1024);
    if (tooBig.length) { setMsg(`Too large (max 25 MB here): ${tooBig.map((f) => f.name).join(", ")}`); return; }
    setBusy(true); setMsg(`Uploading ${files.length} file(s)…`);
    try {
      const payload = await Promise.all(files.map(async (f) => {
        const buf = new Uint8Array(await f.arrayBuffer());
        let bin = ""; for (let i = 0; i < buf.length; i += 0x8000) bin += String.fromCharCode(...buf.subarray(i, i + 0x8000));
        return { name: f.name, b64: btoa(bin) };
      }));
      const r = (await upload({ companyId, collection: col, files: payload })) as { saved: string[]; rejected: string[] };
      setMsg(`Saved ${r.saved.length}${r.rejected.length ? `; rejected: ${r.rejected.join("; ")}` : ""}. Indexing started.`);
      refresh();
    } catch (e) { setMsg(`Upload failed: ${errText(e)}`); }
    setBusy(false);
  }

  return (
    <div className="space-y-4">
      <div className={card}>
        <h2 className="text-[15px] font-semibold">Upload documents</h2>
        <p className="text-[13px] text-muted-foreground">PDF, Word, Excel, Markdown or text. Files are indexed straight away. Anonymised collections replace known customer names.</p>
        <label className={label}>Collection</label>
        <select className={input} value={col} onChange={(e) => setCol(e.target.value)}>
          {uploadable.map((c) => <option key={c.name} value={c.name}>{c.name} — {c.description}</option>)}
        </select>
        <label
          onDragOver={(e) => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)}
          onDrop={(e) => { e.preventDefault(); setOver(false); void send(e.dataTransfer.files); }}
          style={over ? { borderColor: BRAND } : undefined}
          className={`mt-3 block cursor-pointer rounded-lg border-2 border-dashed p-8 text-center text-[13px] ${over ? "bg-accent" : "border-border text-muted-foreground"}`}>
          {busy ? "Uploading…" : "Drop files here or click to choose"}
          <input type="file" multiple hidden accept={data.file_types.join(",")} onChange={(e) => void send(e.target.files)} />
        </label>
        {msg && <p className="mt-2 text-[12px] text-muted-foreground">{msg}</p>}
      </div>
      <div className={card}>
        <h2 className="mb-2 text-[15px] font-semibold">Files in {col}</h2>
        {current && current.files.length ? (
          <table className="w-full text-[13px]"><tbody>
            {current.files.map((f) => (
              <tr key={f.name} className="border-t border-border first:border-t-0">
                <td className="py-2 pr-3">{f.name}</td>
                <td className="py-2 text-muted-foreground">{size(f.size)}</td>
                <td className="py-2 text-muted-foreground">{new Date(f.modified * 1000).toLocaleString()}</td>
                <td className="py-2 text-right"><button className={btn} style={{ color: "#dc2626" }} onClick={async () => {
                  if (!window.confirm(`Delete ${f.name} from ${col}?`)) return;
                  try { await del({ companyId, collection: col, name: f.name }); toast({ title: "Deleted; re-indexing" }); refresh(); }
                  catch (e) { toast({ title: "Delete failed", body: errText(e), tone: "error" }); }
                }}>Delete</button></td>
              </tr>
            ))}
          </tbody></table>
        ) : <p className="text-[13px] text-muted-foreground">No uploaded files yet.</p>}
      </div>
    </div>
  );
}

function Models({ data, companyId, refresh }: SectionProps) {
  const [edits, setEdits] = useState<Record<string, Partial<Model> & { api_key?: string }>>({});
  const [tests, setTests] = useState<Record<string, string>>({});
  const save = usePluginAction("saveModel");
  const test = usePluginAction("testModel");
  const toast = usePluginToast();
  return (
    <div className="space-y-4">
      <div className={card}>
        <h2 className="text-[15px] font-semibold">Where the models run</h2>
        <p className="text-[13px] text-muted-foreground">Every model is reached through an OpenAI-compatible API. Pick a preset, review, then save each card. A new embedding model re-embeds every collection (slow on a CPU, fast on a GPU).</p>
        <div className="mt-3 flex flex-wrap gap-2">
          {Object.keys(PRESETS).map((p) => <button key={p} className={btn} onClick={() => { setEdits(JSON.parse(JSON.stringify(PRESETS[p]))); toast({ title: `${p} preset filled in — review and save` }); }}>{p}</button>)}
        </div>
      </div>
      <div className="grid gap-4 md:grid-cols-3">
        {ROLES.map(({ key, title, hint }) => {
          const m = { ...data.models[key], ...(edits[key] ?? {}) } as Model & { api_key?: string };
          const set = (k: string, v: unknown) => setEdits((e) => ({ ...e, [key]: { ...(e[key] ?? {}), [k]: v } }));
          return (
            <div key={key} className={card}>
              <h3 className="text-[14px] font-semibold">{title}</h3>
              <p className="text-[12px] text-muted-foreground">{hint}</p>
              <label className={label}>Provider</label>
              <select className={input} value={m.provider} onChange={(e) => set("provider", e.target.value)}>
                <option value="openai">OpenAI-compatible API</option>
                {key === "rerank" && <option value="local">local (CPU, inside the service)</option>}
                <option value="none">off</option>
              </select>
              <label className={label}>Base URL</label>
              <input className={input} value={m.base_url ?? ""} onChange={(e) => set("base_url", e.target.value)} placeholder="http://ollama:11434/v1" />
              <label className={label}>Model</label>
              <input className={input} value={m.model ?? ""} onChange={(e) => set("model", e.target.value)} />
              <label className={label}>API key {data.models[key].api_key_set ? "· set" : "· none"}</label>
              <input className={input} type="password" value={m.api_key ?? ""} onChange={(e) => set("api_key", e.target.value)} placeholder={data.models[key].api_key_set ? "leave empty to keep" : "optional"} />
              {key === "rerank" && (<><label className={label}>Candidates rescored</label>
                <input className={input} type="number" value={m.candidates ?? 12} onChange={(e) => set("candidates", e.target.value)} /></>)}
              <div className="mt-4 flex gap-2">
                <button className={btnPrimary} onClick={async () => {
                  try {
                    const body: Record<string, unknown> = { role: key, provider: m.provider, base_url: m.base_url, model: m.model };
                    if (m.api_key) body.api_key = m.api_key;
                    if (key === "rerank") body.candidates = m.candidates;
                    const r = (await save({ companyId, settings: body })) as { reembedding?: boolean };
                    setEdits((e) => { const n = { ...e }; delete n[key]; return n; });
                    toast({ title: r.reembedding ? "Saved — re-embedding all collections" : "Saved" }); refresh();
                  } catch (e) { toast({ title: "Save failed", body: errText(e), tone: "error" }); }
                }}>Save</button>
                <button className={btn} onClick={async () => {
                  setTests((t) => ({ ...t, [key]: "Testing…" }));
                  try { const r = (await test({ companyId, role: key })) as { ok: boolean; detail: string; ms?: number };
                    setTests((t) => ({ ...t, [key]: `${r.ok ? "OK" : "Failed"} — ${r.detail}${r.ms != null ? ` · ${r.ms} ms` : ""}` })); }
                  catch (e) { setTests((t) => ({ ...t, [key]: `Failed — ${errText(e)}` })); }
                }}>Test</button>
              </div>
              {tests[key] && <p className="mt-2 text-[12px] text-muted-foreground">{tests[key]}</p>}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function Search({ data, companyId }: { data: Overview; companyId?: string }) {
  const [q, setQ] = useState("");
  const [col, setCol] = useState("");
  const [res, setRes] = useState<{ results: Hit[]; mode: string; ms: number } | null>(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const search = usePluginAction("search");
  async function go() {
    if (!q.trim()) return;
    setBusy(true); setErr("");
    try { setRes((await search({ companyId, query: q, collection: col, top_k: 6 })) as { results: Hit[]; mode: string; ms: number }); }
    catch (e) { setErr(errText(e)); }
    setBusy(false);
  }
  return (
    <div className={card}>
      <h2 className="text-[15px] font-semibold">Test search</h2>
      <p className="text-[13px] text-muted-foreground">Exactly what the agents get from the search tool.</p>
      <div className="mt-3 flex flex-wrap gap-2">
        <input className={`${input} min-w-[240px] flex-1`} value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") void go(); }} placeholder="e.g. Can everything run on-premises without internet?" />
        <select className={`${input} w-auto`} value={col} onChange={(e) => setCol(e.target.value)}>
          <option value="">all collections</option>
          {data.collections.map((c) => <option key={c.name} value={c.name}>{c.name}</option>)}
        </select>
        <button className={btnPrimary} disabled={busy} onClick={() => void go()}>{busy ? "Searching…" : "Search"}</button>
      </div>
      {err && <p className="mt-2 text-[12px]" style={{ color: "#dc2626" }}>{err}</p>}
      {res && (
        <div className="mt-3">
          <p className="text-[12px] text-muted-foreground">{res.results.length} result(s) · {res.mode} · {res.ms} ms</p>
          {res.results.map((h, i) => (
            <div key={i} className="border-t border-border py-3 first:border-t-0">
              <div className="flex flex-wrap items-center gap-2 text-[13px]">
                <span className="rounded-full bg-muted px-2 py-0.5 text-[11px]">{h.collection}</span>
                <span className="font-semibold">{h.title}</span>
                {h.section && <span className="text-muted-foreground">· {h.section}</span>}
                <span className="ml-auto text-[11px] text-muted-foreground">score {h.score}</span>
              </div>
              <div className="font-mono text-[11px] text-muted-foreground">{h.page || h.source}</div>
              <div className="mt-1 line-clamp-5 whitespace-pre-wrap text-[13px] text-foreground/85">{h.text}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function Access({ data }: { data: Overview }) {
  return (
    <div className={card}>
      <h2 className="text-[15px] font-semibold">Who can search what</h2>
      <p className="mb-3 text-[13px] text-muted-foreground">Each Paperclip connection has its own key, and the key decides the collections. Keys live in the knowledge service's .env and config.yaml.</p>
      <table className="w-full text-[13px]"><tbody>
        {data.clients.map((c) => (
          <tr key={c.name} className="border-t border-border first:border-t-0">
            <td className="py-2 pr-4 font-semibold">{c.name}</td>
            <td className="py-2">{c.collections.map((x) => <span key={x} className="mr-1 inline-block rounded-full bg-muted px-2 py-0.5 text-[11px]">{x}</span>)}</td>
          </tr>
        ))}
      </tbody></table>
    </div>
  );
}
