import axios from 'axios'
import { showFailToast, showLoadingToast } from 'vant'
import router from '@/router'

const api = axios.create({
  baseURL: '/api',
  timeout: 10000,
})

// --- global loading ---
let pendingCount = 0
let loadingTimer: ReturnType<typeof setTimeout> | null = null
let closeLoading: (() => void) | null = null

function showLoading() {
  loadingTimer = setTimeout(() => {
    const toast = showLoadingToast({ message: '加载中...', forbidClick: true, duration: 0 })
    closeLoading = toast.close
  }, 200)
}

function hideLoading() {
  if (loadingTimer) { clearTimeout(loadingTimer); loadingTimer = null }
  if (closeLoading) { closeLoading(); closeLoading = null }
}
// --- end global loading ---

api.interceptors.request.use((config) => {
  const token = localStorage.getItem('token')
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  if (!(config as any).skipLoading) {
    pendingCount++
    if (pendingCount === 1) showLoading()
  }
  return config
})

function toastError(msg: string) {
  setTimeout(() => showFailToast({ message: msg, duration: 2000 }), 0)
}

api.interceptors.response.use(
  (res) => {
    if (!(res.config as any).skipLoading) {
      pendingCount--
      if (pendingCount <= 0) { pendingCount = 0; hideLoading() }
    }
    return res
  },
  (err) => {
    if (!err.config?.skipLoading) {
      pendingCount--
      if (pendingCount <= 0) { pendingCount = 0; hideLoading() }
    }

    if (err.config?.skipGlobalError) {
      return Promise.reject(err)
    }

    const status = err.response?.status
    const detail = err.response?.data?.detail

    if (status === 401) {
      localStorage.removeItem('token')
      // 保存当前路径，登录后跳回
      const currentPath = window.location.pathname + window.location.search || '/'
      if (currentPath !== '/profile') {
        sessionStorage.setItem('loginRedirect', currentPath)
      }
      router.replace('/profile')
      toastError('请先登录')
    } else if (status === 403) {
      toastError(detail || '权限不足')
    } else if (status === 404) {
      toastError(detail || '资源不存在')
    } else if (status === 422) {
      if (Array.isArray(detail)) {
        const msg = detail.map((d: any) => d.msg).join('；')
        toastError(msg || '请求参数错误')
      } else {
        toastError(detail || '请求参数错误')
      }
    } else if (status && status >= 500) {
      toastError('服务器错误，请稍后重试')
    } else if (err.code === 'ECONNABORTED') {
      toastError('请求超时')
    } else if (!err.response) {
      toastError('网络连接失败')
    } else if (detail) {
      toastError(typeof detail === 'string' ? detail : '请求失败')
    }

    return Promise.reject(err)
  }
)

export default api
