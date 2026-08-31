import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    // M7: local dev still runs two processes (Vite :5173, backend-api
    // :8000) -- this proxy makes the frontend's own fetch calls
    // same-origin from the browser's perspective even in dev, so
    // api/client.js never needs a dev-vs-prod branch. The production
    // collapsed image (main.py serving both) needs no proxy at all --
    // this block only affects `npm run dev`.
    proxy: {
      '/auth': 'http://localhost:8000',
      '/macros': 'http://localhost:8000',
      '/executions': 'http://localhost:8000',
      '/users': 'http://localhost:8000',
      '/health': 'http://localhost:8000',
    },
  },
})
