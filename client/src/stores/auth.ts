import { defineStore } from 'pinia'
import { ref } from 'vue'
import api from '@/api/client'

export interface UserProfile {
  id: number
  username: string
  email?: string | null
  avatar: string
  gender?: string | null
  role: string
  // false = 账号已被管理员禁用。本人被禁用时后端直接拒绝登录/鉴权，
  // 这里只用于管理面板等展示场景
  is_active?: boolean
  invite_code?: string | null
}

export const useAuthStore = defineStore('auth', () => {
  const user = ref<UserProfile | null>(null)
  const token = ref<string | null>(localStorage.getItem('token'))

  function setAuth(t: string, u: UserProfile) {
    token.value = t
    user.value = u
    localStorage.setItem('token', t)
  }

  async function logout() {
    // 先通知后端递增 token 版本号，使当前 token 立即失效
    try {
      await api.post('/auth/logout', null, { skipGlobalError: true } as any)
    } catch { /* token 可能已失效，忽略 */ }
    token.value = null
    user.value = null
    localStorage.removeItem('token')
  }

  async function fetchMe(skipLoading = false) {
    if (!token.value) return
    try {
      const res = await api.get('/auth/me', { skipLoading } as any)
      user.value = res.data
    } catch (e: any) {
      // 只有凭证真的失效才登出。logout() 会调后端把 token_version 自增，
      // 是「永久作废」操作：若对 5xx / 502 / 网络错误 / 超时也这么做，
      // 一次后端重启或网关抖动就会让用户丢掉 90 天登录态。
      const status = e?.response?.status
      if (status === 401 || status === 403) await logout()
    }
  }

  async function login(username: string, password: string) {
    const res = await api.post('/auth/login', { username, password })
    setAuth(res.data.access_token, res.data.user)
  }

  async function register(username: string, password: string, gender: string, email: string, invite_code?: string, init_code?: string) {
    const res = await api.post('/auth/register', {
      username,
      password,
      gender,
      invite_code: invite_code || null,
      init_code: init_code || null,
      email: email || null,
    })
    setAuth(res.data.access_token, res.data.user)
  }

  return { user, token, setAuth, logout, fetchMe, login, register }
})
