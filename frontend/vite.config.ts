import { defineConfig } from "vite";
import { fileURLToPath } from "node:url";

// The bundle is served by FastAPI from src/footage/static/orbit/ (committed, so
// `footage serve` needs no Node).  Fixed file names; the template adds ?v=<hash>.
export default defineConfig(({ mode }) => ({
  build: {
    outDir: fileURLToPath(new URL("../src/footage/static/orbit", import.meta.url)),
    emptyOutDir: true,
    target: "es2020",
    sourcemap: mode === "development",
    chunkSizeWarningLimit: 800,
    minify: mode !== "development",
    cssCodeSplit: false,
    rollupOptions: {
      input: fileURLToPath(new URL("./src/main.ts", import.meta.url)),
      output: {
        format: "es",
        entryFileNames: "archive.js",
        chunkFileNames: "[name].js",
        assetFileNames: "archive[extname]",
      },
    },
  },
}));
