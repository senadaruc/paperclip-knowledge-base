import esbuild from "esbuild";
import { createPluginBundlerPresets } from "@paperclipai/plugin-sdk/bundlers";
const p = createPluginBundlerPresets({ uiEntry: "src/ui/index.tsx", sourcemap: false });
await esbuild.build(p.esbuild.worker);
await esbuild.build(p.esbuild.manifest);
await esbuild.build({ ...p.esbuild.ui, jsx: "automatic" });
console.log("built");
