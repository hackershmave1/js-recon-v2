/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Same-origin in dev: proxy the API prefixes to FastAPI on :8000 so the browser
// (and the fetch-based SSE reader) never crosses origins → no CORS, header rides.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/runs": "http://localhost:8000",
      "/sessions": "http://localhost:8000",
      "/healthz": "http://localhost:8000",
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/setupTests.ts"],
  },
});
