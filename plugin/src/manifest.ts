import type { PaperclipPluginManifestV1 } from "@paperclipai/plugin-sdk";

const manifest: PaperclipPluginManifestV1 = {
  id: "community.knowledge-base",
  apiVersion: 1,
  version: "0.2.0",
  displayName: "Knowledge Base",
  description: "Manage the knowledge base (RAG) that Paperclip agents search: collections, document uploads, model locations and test search.",
  author: "Senad Aruc",
  categories: ["ui"],
  capabilities: ["ui.page.register", "ui.sidebar.register", "http.outbound", "secrets.read-ref"],
  entrypoints: { worker: "./dist/worker.js", ui: "./dist/ui" },
  instanceConfigSchema: {
    type: "object",
    properties: {
      ragUrl: { type: "string", title: "RAG service URL", default: "http://knowledge-base:8790" },
      adminKeyRef: { type: "object", format: "secret-ref", title: "Knowledge service admin key (secret reference)" },
    },
  },
  ui: {
    slots: [
      { type: "page", id: "knowledge-page", displayName: "Knowledge", exportName: "KnowledgePage", routePath: "knowledge" },
      { type: "sidebar", id: "knowledge-sidebar", displayName: "Knowledge", exportName: "KnowledgeSidebarLink" },
    ],
  },
};

export default manifest;
