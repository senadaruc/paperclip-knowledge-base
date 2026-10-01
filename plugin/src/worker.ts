import { definePlugin, runWorker } from "@paperclipai/plugin-sdk";
import type { PluginContext } from "@paperclipai/plugin-sdk";

type Cfg = { ragUrl?: string; adminKeyRef?: unknown; adminKey?: string };

async function loadConfig(ctx: PluginContext, companyId?: string): Promise<Cfg> {
  let cfg: unknown = null;
  try { cfg = await ctx.config.get(companyId); } catch { /* instance config only */ }
  if (!cfg) { try { cfg = await ctx.config.get(); } catch { cfg = {}; } }
  return (cfg ?? {}) as Cfg;
}

function isRef(v: unknown): v is { type: "secret_ref"; secretId: string } {
  return typeof v === "object" && v !== null && (v as { type?: unknown }).type === "secret_ref";
}

async function rag(ctx: PluginContext, companyId: string | undefined, path: string, init: RequestInit = {}) {
  const cfg = await loadConfig(ctx, companyId);
  const base = (cfg.ragUrl || "http://knowledge-base:8790").replace(/\/+$/, "");
  let key = "";
  if (isRef(cfg.adminKeyRef)) key = await ctx.secrets.resolve(cfg.adminKeyRef as never, { companyId, configPath: "adminKeyRef" });
  else if (typeof cfg.adminKey === "string") key = cfg.adminKey;
  if (!key) throw new Error("The knowledge service admin key is not configured in the plugin settings.");
  const res = await fetch(base + path, { ...init, headers: { ...(init.headers as Record<string, string> | undefined), Authorization: `Bearer ${key}` } });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error((body as { error?: string }).error || `Knowledge service returned ${res.status}`);
  return body;
}

const json = (b: unknown): RequestInit => ({ method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(b) });
const cid = (p: Record<string, unknown> | undefined) => (typeof p?.companyId === "string" ? p.companyId : undefined);

const plugin = definePlugin({
  async setup(ctx) {
    ctx.data.register("overview", async (p) => rag(ctx, cid(p), "/admin/api/overview"));
    ctx.actions.register("reindex", async (p) =>
      rag(ctx, cid(p), `/admin/reindex?force=1&collection=${encodeURIComponent(String(p.collection ?? ""))}`, { method: "POST" }));
    ctx.actions.register("saveModel", async (p) => rag(ctx, cid(p), "/admin/api/settings", json(p.settings)));
    ctx.actions.register("testModel", async (p) => rag(ctx, cid(p), "/admin/api/test", json({ role: p.role })));
    ctx.actions.register("search", async (p) =>
      rag(ctx, cid(p), "/admin/api/search", json({ query: p.query, collection: p.collection, top_k: p.top_k ?? 6 })));
    ctx.actions.register("deleteFile", async (p) => rag(ctx, cid(p), "/admin/api/delete", json({ collection: p.collection, name: p.name })));
    ctx.actions.register("upload", async (p) => {
      const files = (p.files ?? []) as { name: string; b64: string }[];
      const fd = new FormData();
      fd.append("collection", String(p.collection ?? ""));
      for (const f of files) fd.append("files", new Blob([Buffer.from(f.b64, "base64")]), f.name);
      return rag(ctx, cid(p), "/admin/api/upload", { method: "POST", body: fd });
    });
    ctx.logger.info("knowledge base plugin ready");
  },
  async onHealth() {
    return { status: "ok", message: "Knowledge Base plugin ready" };
  },
});

export default plugin;
runWorker(plugin, import.meta.url);
