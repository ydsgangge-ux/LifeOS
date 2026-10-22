import { fileURLToPath, URL } from 'node:url'

import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

// 开发期代理目标。核心进程的端口是随机的（第 7.3 节），开发时用环境变量指过去：
//   $env:LIFEOS_CORE_URL = "http://127.0.0.1:51234"
const coreUrl = process.env.LIFEOS_CORE_URL ?? 'http://127.0.0.1:8000'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
    // 表单与字典直接读仓库根的 schemas/（第 6 章：前后端共用同一份定义）
    fs: { allow: ['..', '../..'] },
    proxy: {
      '/api': { target: coreUrl, changeOrigin: false },
    },
  },
  build: {
    // dist 由核心进程用 StaticFiles 同源托管（第 3.4 节）
    outDir: 'dist',
    emptyOutDir: true,
    assetsDir: 'assets',
  },
  test: {
    environment: 'node',
    include: ['src/**/*.test.ts'],
  },
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
})
