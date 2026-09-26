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
  registration_open_at?: string | null
  created_at: string
}

export const useTournamentStore = defineStore('tournament', () => {
  const list = ref<TournamentBrief[]>([])
  const loading = ref(false)
  const total = ref(0)
  const hasMore = ref(false)
  const page = ref(0)
  const PAGE_SIZE = 20

  /**
   * 拉取赛事列表。
   * @param status 状态筛选
   * @param reset  true = 从第一页重新加载（首次进入/下拉刷新）；false = 追加下一页
   *
   * 原先每次都是「整体覆盖 + 不分页」，而首页的 van-list 又永远不会请求第二页，
   * 所以列表其实是一次性全量返回。
   */
  async function fetchList(status?: string, reset = true, skipLoading = false) {
    loading.value = true
    try {
      const target = reset ? 1 : page.value + 1
      const params: Record<string, unknown> = { skip: (target - 1) * PAGE_SIZE, limit: PAGE_SIZE }
      if (status) params.status = status
      const res = await api.get('/tournaments', { params, skipLoading } as any)
      const data = res.data || {}
      const items: TournamentBrief[] = Array.isArray(data.items) ? data.items : []
      list.value = reset ? items : [...list.value, ...items]
      total.value = typeof data.total === 'number' ? data.total : list.value.length
      hasMore.value = !!data.has_more
      page.value = target
    } finally {
      loading.value = false
    }
  }

  function reset() {
    list.value = []
    total.value = 0
    hasMore.value = false
    page.value = 0
  }

  return { list, loading, total, hasMore, page, fetchList, reset }
})
