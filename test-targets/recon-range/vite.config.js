import { defineConfig } from "vite";
export default defineConfig({
  build: { outDir: "dist/vite", sourcemap: true, emptyOutDir: true, minify: "esbuild" },
});
