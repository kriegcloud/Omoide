/// <reference types="vite/client" />
/// <reference types="vite/types/importMeta.d.ts" />

import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";
import { readFile, writeFile } from "node:fs/promises";
import { gzipSync } from "node:zlib";

// BEGIN transport precompression
function precompressTextAssets(): Plugin {
  return {
    name: "omoide-precompress-text",
    apply: "build",
    async writeBundle(options, bundle) {
      // Emit alongside the exact hashed generation, including lazy chunks.
      // No package/plugin dependency and no compression work on an asset GET.
      for (const filename of Object.keys(bundle)) {
        if (!/\.(js|css|json|svg|txt)$/.test(filename)) continue;
        const target = path.resolve(options.dir || "dist", filename);
        const original = await readFile(target);
        if (original.length < 1000) continue;
        const compressed = gzipSync(original, { level: 9 });
        if (compressed.length < original.length) {
          await writeFile(`${target}.gz`, compressed);
        }
      }
    },
  };
}
// END transport precompression

export default defineConfig(({ mode, command }) => {
  const domain = process.env.DOMAIN || "http://localhost:8123/";

  // ---- START DEBUG LOG ----
  console.log(`[vite.config.js] Mode: ${mode}`);
  console.log(`[vite.config.js] Domain is set to: ${domain}`);
  // ---- END DEBUG LOG ----
  let base: string = "/";
  if (mode == "production") {
    base = "/static/";
  }

  return {
    base: base,
    plugins: [react(), precompressTextAssets()],
    // Keep component/function names in production so React Grab (Alt+Shift+G)
    // reports readable component names instead of minified identifiers.
    esbuild: { keepNames: true },
    server: {
      port: 5173,
      proxy: {
        "/api": {
          target: `${domain}`,
        },
        // Dev-only: serve thumbnails/originals/static through the dev origin so
        // the hardened workstation backend sees same-origin requests.
        "/thumbnails": {
          target: `${domain}`,
        },
        "/originals": {
          target: `${domain}`,
        },
        "/static": {
          target: `${domain}`,
        },
      },
    },
    build: {
      minify: "esbuild",
      // Source maps are only fetched by devtools/React Grab, never by normal
      // page loads; they let annotations resolve to src/<file>.tsx:<line>.
      sourcemap: true,
    },
    resolve: {
      alias: {
        "@": path.resolve(__dirname, "src"),
      },
    },
  };
});
