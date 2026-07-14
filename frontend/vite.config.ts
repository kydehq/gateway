import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import path from "node:path";

// Dev flow: Vite on :5173 proxies auth / API paths to the FastAPI dashboard
// container on :8501 so cookies and redirects behave as if same-origin.
// `base: "./"` keeps the built bundle mountable at any path under nginx.
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  base: "./",
  server: {
    port: 5173,
    proxy: {
      "/api":             "http://localhost:8501",
      "/login":           "http://localhost:8501",
      "/logout":          "http://localhost:8501",
      "/setup":           "http://localhost:8501",
      "/change-password": "http://localhost:8501",
      "/whoami":          "http://localhost:8501",
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
    target: "es2022",
    sourcemap: false,
  },
  // Vitest. Reuses the `@` alias above so tests resolve imports exactly as the
  // app does. Tests use explicit `import { describe, it, expect } from "vitest"`
  // (globals: false) to keep the strict `tsc --noEmit` build and ESLint clean.
  test: {
    environment: "jsdom",
    globals: false,
    setupFiles: ["./src/test/setup.ts"],
    css: false,
    include: ["src/**/*.{test,spec}.{ts,tsx}"],
    restoreMocks: true,
    coverage: {
      // json-summary feeds the CI coverage badge (see .github/workflows/ci.yml).
      reporter: ["text", "json-summary"],
    },
  },
});
