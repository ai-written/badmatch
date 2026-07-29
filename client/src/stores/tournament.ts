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
  entry_fee: number
  status: string
  registered_count: number
  created_at: string
}

export const useTournamentStore = defineStore('tournament', () => {
  const list = ref<TournamentBrief[]>([])
  const loading = ref(false)

  async function fetchList(status?: string) {
    loading.value = true
    try {
      const params = status ? { status } : {}
      const res = await api.get('/tournaments', { params })
      list.value = res.data
    } finally {
      loading.value = false
    }
  }

  return { list, loading, fetchList }
})
