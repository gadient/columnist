import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev server proxies API calls to the FastAPI backend. In production the SPA is served from S3 via
// CloudFront, which routes /api/* to the ALB (same-origin — see docs/DEPLOY.md).
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api/v1": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
});
