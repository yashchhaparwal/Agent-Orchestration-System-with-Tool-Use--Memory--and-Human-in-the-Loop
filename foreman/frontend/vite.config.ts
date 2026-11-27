import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

export default defineConfig(({ mode }) => {
  // API_KEY is read from foreman/.env and added by the dev server, so it never reaches browser code.
  const env = loadEnv(mode, path.resolve(__dirname, '..'), 'API_')

  return {
    plugins: [react()],
    resolve: {
      alias: {
        '@': path.resolve(__dirname, './src'),
      },
    },
    server: {
      // Local only: whoever can reach this proxy can act with the API key.
      host: '127.0.0.1',
      port: 3000,
      strictPort: true,
      cors: false,
      proxy: {
        // Proxy API calls to the FastAPI backend in dev
        '/api': {
          target: env.API_BASE_URL || 'http://127.0.0.1:8000',
          changeOrigin: true,
          rewrite: (p) => p.replace(/^\/api/, ''),
          configure: (proxy) => {
            proxy.on('proxyReq', (proxyReq, req) => {
              // Other websites open in the same browser must not borrow the key.
              const site = req.headers['sec-fetch-site']
              if (env.API_KEY && (!site || site === 'same-origin' || site === 'none')) {
                proxyReq.setHeader('X-API-Key', env.API_KEY)
              }
            })
          },
        },
      },
    },
  }
})
