<template>
  <div class="home-page vh-page">
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
// 解构 store 的 state 会丢响应式，因此列表用本地 ref 镜像；
// loading 则完全由 van-list 通过 v-model:loading 接管：
// 它在 check() 里置为 true，并要求 @load 处理函数结束时置回 false，
// 否则内部 loading 一直为 true，之后不会再触发加载（分页失效）。
// 注意不能用 ref(store.xxx) 去「共享」另一个 ref —— 那只是取值，不是同一个引用。
const list = ref(store.list)
const loading = ref(false)
const refreshing = ref(false)
const finished = ref(false)
// van-list 的错误态：置位后停止自动加载，点错误文案可重试
const loadError = ref(false)
const firstLoadFailed = ref(false)
// 是否已经发起过首次加载（同步标记，见 onLoad 的说明）
let firstLoadDone = false

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
  // van-list 在挂载阶段会连续触发多次 @load：初始 check 一次，之后每次
  // loading/finished/error 变化又会重新 check（见 vant 的
  // watch(() => [props.loading, props.finished, props.error], check)）。
  // 这些触发都在首个请求完成【之前】，所以不能用「请求完成后的状态」判断首次，
  // 也不能让它们并发 —— 否则会各自请求一次、page 被重复自增。
  //
  // 处理办法：
  //   1) 用同步标记判断首次（第一次调用进入时立刻置位）
  //   2) 请求在途时直接返回；van-list 会在 loading 复位后再 check，届时会重新触发
  const reset = !firstLoadDone
  if (!reset && store.isBusy()) return
  firstLoadDone = true
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
  } finally {
    // 必须置回 false（会通过 v-model:loading 通知 van-list 本次加载结束）
    loading.value = false
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
    // 注意：这里不要再动 loading。它是 van-list 通过 v-model:loading 管理的，
    // 而 vant 会 watch loading 的变化再次 check()；在刷新请求结束时手动置 false
    // 会在「追加请求仍在途」时解锁 van-list，导致同一页被追加两次。
    refreshing.value = false
  }
}

</script>

<style scoped>
.home-page { display: flex; flex-direction: column; background: #f5f6f8; }
.home-scroll { flex: 1; overflow-y: auto; }
.pull-fill { min-height: 100%; }
/* 底部固定 tabbar 的占位：含系统安全区与浏览器工具栏（--tabbar-reserve 见 main.css） */
.pull-inner { padding-bottom: var(--tabbar-reserve); }

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
