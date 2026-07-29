<template>
  <div class="home-page">
    <!-- Header -->
    <div class="top-header">
      <div class="header-title">
        <van-icon name="location-o" size="16" />
        <span>羽毛球赛事</span>
      </div>
      <van-icon name="plus" size="22" class="add-btn" @click="$router.push('/create')" />
    </div>

    <!-- List -->
    <van-pull-refresh v-model="refreshing" @refresh="onRefresh">
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
            </div>
            <div class="t-card-date">{{ formatDate(t.start_date) }} ~ {{ formatDate(t.end_date) }}</div>
          </div>
          <div class="t-card-right">
            <van-tag :type="statusType(t.status)" size="medium" round>{{ statusLabel(t.status) }}</van-tag>
            <div class="t-card-count">{{ t.registered_count }}/{{ t.max_participants }}</div>
            <span v-if="t.entry_fee > 0" class="t-card-fee">¥{{ Math.round(t.entry_fee / 100) }}</span>
          </div>
        </div>
      </van-list>
    </van-pull-refresh>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useTournamentStore } from '@/stores/tournament'

const store = useTournamentStore()
const refreshing = ref(false)
const finished = ref(false)

function statusType(s: string) { return s === 'open' ? 'primary' : s === 'ongoing' ? 'success' : 'default' }
function statusLabel(s: string) { return s === 'open' ? '报名中' : s === 'ongoing' ? '进行中' : '已结束' }
function formatDate(d: string) { if (!d) return ''; return d.replace('T', ' ').slice(5, 16) }

async function onLoad() {
  await store.fetchList()
  finished.value = true
}

async function onRefresh() {
  finished.value = false
  store.list = []
  await store.fetchList()
  refreshing.value = false
  finished.value = true
}

onMounted(() => onLoad())
</script>

<style scoped>
.home-page {  background: #f5f6f8; }

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
.t-card-right { text-align: center; flex-shrink: 0; margin-left: 10px; }
.t-card-count { font-size: 13px; color: #666; margin-top: 4px; }
.t-card-fee { font-size: 13px; color: #e74c3c; font-weight: 600; }
</style>
