import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vite'

// /api 前缀代理到 FastAPI（ rewrite 掉 /api 前缀），前端无需关心后端端口与 CORS
export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
})
