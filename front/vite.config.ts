import { fileURLToPath, URL } from 'node:url'

import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    port: 5173,
    // 开发期把 /api 代理到后端，避免跨域；生产由 Nginx 承担同样职责
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        // SSE 是长连接，代理必须关闭缓冲，否则事件会被攒着不推送
        configure: (proxy) => {
          proxy.on('proxyRes', (proxyRes) => {
            if (proxyRes.headers['content-type']?.includes('text/event-stream')) {
              proxyRes.headers['x-accel-buffering'] = 'no'
            }
          })
        },
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
    chunkSizeWarningLimit: 1200,
    rollupOptions: {
      output: {
        // 把体积大的库单独分包，避免首屏加载一个巨大的 chunk。
        // 用函数形式而非对象映射：新版 rollup 的类型只接受函数。
        manualChunks(id: string) {
          if (!id.includes('node_modules')) return undefined
          if (id.includes('echarts') || id.includes('zrender')) return 'echarts'
          if (id.includes('element-plus') || id.includes('@element-plus')) return 'element'
          if (id.includes('/vue/') || id.includes('vue-router') || id.includes('pinia')) {
            return 'vue'
          }
          return undefined
        },
      },
    },
  },
})
