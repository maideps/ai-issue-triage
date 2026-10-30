/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  server: {
    // 5173 falls in a Windows excluded port range on some machines; PORT overrides.
    port: Number(process.env.PORT ?? 4173),
    // The FastAPI backend serves /api; the UI never talks to GitHub or a model directly.
    proxy: { "/api": "http://localhost:8000", "/health": "http://localhost:8000" },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
  },
});
