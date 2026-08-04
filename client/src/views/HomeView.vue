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
      <van-list v-model:loading="store.loading" :finished="finished" @load="onLoad">
        <div
          v-for="t in store.list"
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
            <van-tag :type="statusType(t.status)" size="medium" round>{{ statusLabel(t.status) }}</van-tag>
            <div class="t-card-count">{{ t.registered_count }}/{{ t.max_participants }}</div>
          </div>
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
const refreshing = ref(false)
const finished = ref(false)

function statusType(s: string) { return s === 'open' ? 'primary' : s === 'ongoing' ? 'success' : 'default' }
function statusLabel(s: string) { return s === 'open' ? '报名中' : s === 'ongoing' ? '进行中' : '已结束' }
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
  finished.value = true
  await store.fetchList()
}

async function onRefresh() {
  finished.value = false
  store.list = []
  try {
    await store.fetchList()
  } finally {
    refreshing.value = false
    finished.value = true
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
</style>
