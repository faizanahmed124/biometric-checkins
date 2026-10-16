import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Dev mode mein /api calls backend (port 8800) par jayengi
export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy: { '/api': 'http://127.0.0.1:8800' } },
})
