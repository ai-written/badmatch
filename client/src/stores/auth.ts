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
    } catch { logout() }
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
