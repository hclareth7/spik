import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Dev proxy: forward the same-origin API + video paths to the FastAPI backend so the
// browser never talks to a different origin (honors the "everything local" constraint).
//
// SSE (text/event-stream) and MJPEG (multipart/x-mixed-replace) must NOT be buffered:
// node-http-proxy (what Vite uses under the hood) streams proxied responses as they
// arrive by default and Vite's dev server does not gzip proxied bodies, so no extra
// buffering is introduced. `changeOrigin` rewrites the Host header for the upstream.
const BACKEND = 'http://127.0.0.1:8000';

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': { target: BACKEND, changeOrigin: true },
      '/video': { target: BACKEND, changeOrigin: true },
    },
  },
  build: {
    // FastAPI serves this directory (see web/main.py). Keep in sync with D6.
    outDir: '../web/dist',
    emptyOutDir: true,
  },
});
