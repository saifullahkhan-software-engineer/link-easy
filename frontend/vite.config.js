import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// During development the FastAPI backend is proxied so the browser sees
// same-origin requests (no CORS friction). Override the target with
// VITE_BACKEND_URL if your backend runs elsewhere.
const backendUrl = process.env.VITE_BACKEND_URL || 'http://localhost:8000';

export default defineConfig({
  plugins: [react()],
  // SPA: unknown paths must serve index.html so a browser refresh on
  // /app/account (etc.) does not 404. Vite's default appType is already
  // 'spa'; we set it explicitly so preview/dev stay consistent.
  appType: 'spa',
  server: {
    host: '0.0.0.0',
    port: 5173,
    // Dev previews (e.g. sandboxed environments) reach the app through a
    // hostname that is not localhost — accept any host in development.
    allowedHosts: true,
    proxy: {
      '/api': {
        target: backendUrl,
        changeOrigin: true,
      },
      // Uploaded videos/thumbnails are served by the backend's static mount
      // under /uploads/social/... — proxy those too so the browser can preview
      // a clip on the same origin it talks to the API on (no CORS, works in
      // dev/preview environments that are not localhost).
      '/uploads': {
        target: backendUrl,
        changeOrigin: true,
      },
    },
  },
  preview: {
    port: 4173,
    allowedHosts: true,
  },
});
