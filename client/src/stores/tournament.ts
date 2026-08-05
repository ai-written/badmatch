import { defineStore } from 'pinia'
import { ref } from 'vue'
import api from '@/api/client'

export interface TournamentBrief {
  id: number
  title: string
  location: string | null
  start_date: string
  end_date: string
  max_participants: number
  status: string
  registered_count: number
  court_name: string | null
  created_at: string
}

export const useTournamentStore = defineStore('tournament', () => {
  const list = ref<TournamentBrief[]>([])
  const loading = ref(false)

  async function fetchList(status?: string, skipLoading = false) {
    loading.value = true
    try {
      const params = status ? { status } : {}
      const res = await api.get('/tournaments', { params, skipLoading } as any)
      list.value = res.data
    } finally {
      loading.value = false
    }
  }

  return { list, loading, fetchList }
})
