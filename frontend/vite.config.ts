import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      // dev convenience: same-origin API (mirrors production static-serving)
      "/api": { target: "http://127.0.0.1:8400", changeOrigin: true },
    },
  },
  build: { outDir: "dist", sourcemap: false, chunkSizeWarningLimit: 900 },
});
