import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
export default defineConfig({
  plugins: [vue()],
  base: './',
  resolve: { extensions: ['.mjs', '.ts', '.tsx', '.js', '.jsx', '.json'] },
  server: {
    proxy: {
      '/api': { target: process.env.ZHICAI_API_TARGET || 'http://127.0.0.1:8000', changeOrigin: true },
    },
  },
  build: { rollupOptions: { output: { manualChunks: { charts: ['echarts'], vue: ['vue', 'vue-router', 'pinia'] } } } },
})

