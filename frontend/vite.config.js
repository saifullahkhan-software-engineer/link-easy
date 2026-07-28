import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// During development the FastAPI backend is proxied so the browser sees
// same-origin requests (no CORS friction). Override the target with
// VITE_BACKEND_URL if your backend runs elsewhere.
const backendUrl = process.env.VITE_BACKEND_URL || 'http://localhost:8000';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: backendUrl,
        changeOrigin: true,
      },
    },
  },
});
