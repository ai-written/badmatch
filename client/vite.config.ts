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
