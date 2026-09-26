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
  // UI 的 loading 状态由 van-list 通过 v-model:loading 自己接管
  // （它会在 check() 里置 true，并要求 @load 处理函数置回 false，否则不会再触发加载），
  // 所以这里只用 busy 标记「请求进行中」，不与 v-model 抢同一个状态
  const busy = ref(false)
  const total = ref(0)
  const hasMore = ref(false)
  const page = ref(0)
  const PAGE_SIZE = 20

  // 请求世代号：丢弃迟到响应，避免并发请求各追加一次同一页
  let reqSeq = 0
  // 在途标记：调用方（HomeView）用它忽略请求期间新触发的 @load
  let inFlight = false

  function isBusy() {
    return inFlight
  }

  /**
   * 拉取赛事列表。
   * @param status 状态筛选
   * @param reset  true = 从第一页重新加载（首次进入/下拉刷新）；false = 追加下一页
   *
   * 原先每次都是「整体覆盖 + 不分页」，而首页的 van-list 又永远不会请求第二页，
   * 所以列表其实是一次性全量返回。
   */
  async function fetchList(status?: string, reset = true, skipLoading = false) {
    const seq = ++reqSeq
    inFlight = true
    busy.value = true
    try {
      const target = reset ? 1 : page.value + 1
      const params: Record<string, unknown> = { skip: (target - 1) * PAGE_SIZE, limit: PAGE_SIZE }
      if (status) params.status = status
      const res = await api.get('/tournaments', { params, skipLoading } as any)
      if (seq !== reqSeq) return   // 已有更新的请求发出：本次结果作废
      const data = res.data || {}
      const items: TournamentBrief[] = Array.isArray(data.items) ? data.items : []
      // 列表按 created_at 倒序，翻页期间若有人在头部插入新赛事，offset 会整体下移，
      // 导致同一 id 被追加两次（模板用 :key="t.id"，Vue 会报重复 key）。追加时按 id 去重。
      const merged = reset ? items : [...list.value, ...items]
      const seen = new Set<number>()
      list.value = merged.filter(t => (seen.has(t.id) ? false : (seen.add(t.id), true)))
      total.value = typeof data.total === 'number' ? data.total : list.value.length
      hasMore.value = !!data.has_more
      page.value = target
    } finally {
      if (seq === reqSeq) {
        inFlight = false
        busy.value = false
      }
    }
  }

  function reset() {
    list.value = []
    total.value = 0
    hasMore.value = false
    page.value = 0
  }

  return { list, busy, total, hasMore, page, fetchList, reset, isBusy }
})
