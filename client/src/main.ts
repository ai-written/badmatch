import { createApp } from 'vue'
import { createPinia } from 'pinia'
import { Lazyload } from 'vant'
import App from './App.vue'
import router from './router'
import { installViewportSync } from './utils/viewport'
// Vant 样式按需引入：模板组件样式由 unplugin-vue-components 自动注入；
// 这里只需手动引入函数式调用（showToast/showConfirmDialog/showActionSheet/showCalendar）的样式
import 'vant/es/toast/style'
import 'vant/es/notify/style'
import 'vant/es/dialog/style'
import 'vant/es/action-sheet/style'
import 'vant/es/calendar/style'
import 'vant/es/lazyload/style'
import './assets/main.css'

const app = createApp(App)
app.use(createPinia())
app.use(router)
app.use(Lazyload)
app.mount('#app')

// 同步「真实可见高度」到 CSS 变量（须在 body 存在之后，探针要挂到 body 上）。
// 页面与弹层高度依赖它，所以放在 mount 之后紧接着执行，避免首帧用错高度。
installViewportSync()

// 生产环境注册 Service Worker：静态资源缓存优先 + 离线兜底（需 HTTPS 或 localhost）
if (import.meta.env.PROD && 'serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch((err) => {
      console.warn('Service Worker 注册失败:', err)
    })
  })
}
