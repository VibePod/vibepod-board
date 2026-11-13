import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "dist/client",
    emptyOutDir: true,
  },
  server: {
    host: "0.0.0.0",
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:3000",
      // Also covers /mcp-docs.
      "/mcp": "http://127.0.0.1:3000",
      "/docs": "http://127.0.0.1:3000",
      "/redoc": "http://127.0.0.1:3000",
      "/openapi.json": "http://127.0.0.1:3000",
    },
  },
});
