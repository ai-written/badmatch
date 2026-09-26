<template>
  <div class="home-page">
    <!-- Header -->
    <div class="top-header">
      <div class="header-title">
        <img src="/favicon.png" class="header-logo" />
        <span>爱玩羽社</span>
      </div>
      <van-icon name="plus" size="22" class="add-btn" @click="$router.push('/create')" />
    </div>

    <!-- List -->
    <div class="home-scroll">
    <van-pull-refresh v-model="refreshing" @refresh="onRefresh" class="pull-fill">
      <div class="pull-inner">
      <van-list
        v-model:loading="loading"
        :finished="finished"
        :error="loadError"
        error-text="加载失败，点击重试"
        finished-text="没有更多了"
        @update:error="loadError = false"
        @load="onLoad"
      >
        <div
          v-for="t in list"
          :key="t.id"
          class="t-card"
          @click="$router.push(`/tournament/${t.id}`)"
        >
          <div class="t-card-left">
            <div class="t-card-title">{{ t.title }}</div>
            <div class="t-card-meta">
              <van-icon name="location-o" size="11" />
              {{ t.location || '待定' }}
              <template v-if="t.court_name">
                <span class="t-card-court">场地号{{ t.court_name }}</span>
              </template>
            </div>
            <div class="t-card-date">{{ fmtDateTime(t.start_date, t.end_date) }}</div>
          </div>
          <div class="t-card-right">
            <van-tag :type="statusType(t.status)" size="medium" round>{{ statusLabel(t) }}</van-tag>
            <div class="t-card-count">{{ t.registered_count }}/{{ t.max_participants }}</div>
          </div>
        </div>
        <!-- 首屏就失败时给一个明确的失败态：不要把空列表说成「没有更多了」 -->
        <div v-if="list.length === 0 && firstLoadFailed" class="home-error">
          <van-icon name="warning-o" size="20" />
          <span>加载失败，请下拉重试</span>
        </div>
      </van-list>
    </div>
    </van-pull-refresh>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useTournamentStore } from '@/stores/tournament'

const store = useTournamentStore()
// 解构成本地 ref 而不是继续用 store.xxx：解构 Pinia 的 state 会丢响应式，
// 本地 ref 是同一份引用，赋值后依然能触发视图更新
const list = ref(store.list)
const loading = ref(store.loading)
const refreshing = ref(false)
const finished = ref(false)
// van-list 的错误态：置位后停止自动加载，点错误文案可重试
const loadError = ref(false)
const firstLoadFailed = ref(false)

function statusType(s: string) { return s === 'open' ? 'primary' : s === 'ongoing' ? 'success' : 'default' }
function isRegLocked(t: any) { return !!t.registration_open_at && new Date(t.registration_open_at).getTime() > Date.now() }
function statusLabel(t: any) { return t.status === 'open' ? (isRegLocked(t) ? '未开始' : '报名中') : t.status === 'ongoing' ? '进行中' : '已结束' }
const WEEKDAYS = ['周日', '周一', '周二', '周三', '周四', '周五', '周六']
function fmtDateTime(start: string, end: string) {
  if (!start || !end) return ''
  const d = new Date(start)
  const y = d.getFullYear()
  const m = d.getMonth() + 1
  const day = d.getDate()
  const w = WEEKDAYS[d.getDay()]
  return `${y}年${m}月${day}日(${w}) ${start.slice(11, 16)}~${end.slice(11, 16)}`
}

async function onLoad() {
  // 首次进入：van-list 在挂载时会自动触发一次 @load，与下拉刷新的初始 check 可能重叠；
  // 用 store.page 判断，避免同一次进入把第一页拉两遍
  const reset = store.page === 0
  try {
    await store.fetchList(undefined, reset, !reset)
    list.value = store.list
    firstLoadFailed.value = false
    // 拉完一页后按「是否还有更多」收尾；注意不能在上面的 fetch 之前就置 finished ——
    // 那会让 van-list 的转圈条件（loading && !finished）永远不成立，首屏没有加载提示
    finished.value = !store.hasMore
  } catch {
    if (reset) {
      firstLoadFailed.value = true
      finished.value = true   // 停止自动加载，改为展示失败态 / 下拉重试
    } else {
      loadError.value = true  // 追加失败：保留已加载内容，点错误文案可重试
    }
  }
}

async function onRefresh() {
  try {
    store.reset()
    await store.fetchList(undefined, true, true)
    list.value = store.list
    finished.value = !store.hasMore
    firstLoadFailed.value = false
  } catch {
    firstLoadFailed.value = true
    finished.value = true
  } finally {
    loadError.value = false
    refreshing.value = false
  }
}

</script>

<style scoped>
.home-page { height: 100vh; display: flex; flex-direction: column; background: #f5f6f8; }
.home-scroll { flex: 1; overflow-y: auto; }
.pull-fill { min-height: 100%; }
.pull-inner { padding-bottom: 60px; }

.header-logo { width: 20px; height: 20px; border-radius: 4px; }
.top-header {
  display: flex; align-items: center; justify-content: space-between;
  padding: 12px 16px 6px; background: #1989fa;
}
.header-title { display: flex; align-items: center; gap: 6px; color: #fff; font-size: 16px; font-weight: 600; }
.add-btn { color: #fff; }

.t-card {
  display: flex; align-items: center; justify-content: space-between;
  margin: 8px 12px; padding: 14px; background: #fff; border-radius: 10px;
  box-shadow: 0 1px 3px rgba(0,0,0,.04);
}
.t-card-left { flex: 1; min-width: 0; }
.t-card-title { font-size: 15px; font-weight: 600; margin-bottom: 4px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.t-card-meta { font-size: 12px; color: #999; display: flex; align-items: center; gap: 3px; margin-bottom: 2px; }
.t-card-date { font-size: 11px; color: #bbb; }
.t-card-court { font-size: 11px; color: #1989fa; background: #e8f4ff; padding: 0 4px; border-radius: 3px; margin-left: 6px; }
.t-card-right { text-align: center; flex-shrink: 0; margin-left: 10px; }
.t-card-count { font-size: 13px; color: #666; margin-top: 4px; }
.home-error {
  display: flex; flex-direction: column; align-items: center; gap: 6px;
  padding: 60px 0; color: #969799; font-size: 13px;
}
</style>
