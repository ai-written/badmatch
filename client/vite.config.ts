import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { resolve } from 'path'
import Components from 'unplugin-vue-components/vite'
import { VantResolver } from '@vant/auto-import-resolver'

const API_HOST = process.env.API_HOST || 'localhost'

export default defineConfig({
  plugins: [
    vue(),
    Components({
      resolvers: [VantResolver()],
    }),
  ],
  resolve: {
    alias: {
      '@': resolve(__dirname, 'src'),
    },
  },
  build: {
    rollupOptions: {
      output: {
        // 稳定 vendor 分包：vue/vant/axios 各自独立 chunk，
        // 配合 /assets/ 的 30 天 immutable 缓存，发版时业务代码更新不影响 vendor 缓存
        manualChunks(id) {
          if (!id.includes('node_modules')) return
          // 按包名精确分类（避免 vant 的 vue-lazyload 目录名被误判为 vue）
          // 包路径形如 /@vue/shared/dist/...，首段为空，需过滤
          const parts = id.split('node_modules')[1].replace(/\\/g, '/').split('/').filter(Boolean)
          const pkg = parts[0].startsWith('@') ? `${parts[0]}/${parts[1]}` : parts[0]
          if (pkg === 'vant' || pkg.startsWith('@vant/')) return 'vendor-vant'
          if (pkg === 'vue' || pkg === 'vue-router' || pkg === 'pinia' || pkg === 'vue-demi' || pkg.startsWith('@vue/')) return 'vendor-vue'
          if (pkg === 'axios') return 'vendor-axios'
          return 'vendor-misc'
        },
      },
    },
  },
  server: {
    host: '0.0.0.0',
    watch: {
      // Windows -> Docker bind mount 常不触发文件事件，轮询可保证 HMR 生效
      usePolling: true,
    },
    proxy: {
      '/api': `http://${API_HOST}:8000`,
      '/static': `http://${API_HOST}:8000`,
      '/ws': {
        target: `ws://${API_HOST}:8000`,
        ws: true,
      },
    },
  },
})
