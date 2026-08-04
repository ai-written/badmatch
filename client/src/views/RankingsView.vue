<template>
  <div class="rank-page">
    <van-nav-bar title="积分榜" left-text="返回" left-arrow @click-left="goBack" />

    <div class="rank-scroll">
    <van-pull-refresh v-model="refreshing" @refresh="onRefresh" class="pull-fill">
      <div class="pull-inner">
    <van-loading v-if="!ranking" class="loading" />
    <template v-else>
      <div class="rank-head">
        <h3>{{ ranking.tournament_title }}</h3>
        <span class="sub">双打胜率</span>
      </div>

      <div class="rank-table">
        <div
          v-for="p in visibleRankings"
          :key="p.user_id"
          class="rank-row"
          :class="{ active: p.user_id === auth.user?.id, dropped: !p.is_active }"
        >
          <span class="col-rank">
            <template v-if="p.is_active && p.rank === 1">🥇</template>
            <template v-else-if="p.is_active && p.rank === 2">🥈</template>
            <template v-else-if="p.is_active && p.rank === 3">🥉</template>
            <template v-else>{{ p.is_active ? p.rank : '-' }}</template>
          </span>
          <div class="col-player">
            <van-image round width="28" height="28" :src="p.avatar || defaultAvatar" />
            <span class="p-name">{{ p.username }}</span>
          </div>
          <span class="col-wl"><em class="wl-win">{{ p.matches_won }}</em>-{{ p.matches_lost }}</span>
          <span class="col-diff" :class="p.point_diff >= 0 ? 'positive' : 'negative'">{{ p.point_diff > 0 ? '+' : '' }}{{ p.point_diff }}</span>
          <span class="col-rate">{{ winRate(p) }}%</span>
        </div>
      </div>
    </template>
      </div>
    </van-pull-refresh>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import api from '@/api/client'
import { useWebSocket } from '@/composables/useWebSocket'
import { useGoBack } from '@/composables/useGoBack'

const route = useRoute()
const auth = useAuthStore()
const tid = Number(route.params.id)
const { lastMessage } = useWebSocket(tid)
const { goBack } = useGoBack()
const refreshing = ref(false)
const ranking = ref<any>(null)
const defaultAvatar = 'https://img.yzcdn.cn/vant/cat.jpeg'

const visibleRankings = computed(() => {
  if (!ranking.value) return []
  const active = ranking.value.rankings.filter((p: any) => p.is_active)
  const dropped = ranking.value.rankings.filter((p: any) => !p.is_active)
  return [...active, ...dropped]
})

function winRate(p: any) {
  if (p.matches_played === 0) return 0
  return Math.round((p.matches_won / p.matches_played) * 100)
}

async function fetchRankings() {
  const res = await api.get(`/tournaments/${route.params.id}/rankings`)
  ranking.value = res.data
}

async function onRefresh() {
  try { await fetchRankings() } finally { refreshing.value = false }
}
watch(lastMessage, (msg) => { if (msg?.type === 'match_updated') fetchRankings() })
onMounted(async () => {
  await auth.fetchMe()
  await fetchRankings()
})
</script>

<style scoped>
.rank-page { height: 100vh; display: flex; flex-direction: column; background: #f0f2f5; }
.rank-scroll { flex: 1; overflow-y: auto; }
.pull-fill { min-height: 100%; }
.pull-inner { padding-bottom: 60px; }
.loading { display: flex; justify-content: center; margin-top: 100px; }
.rank-head { padding: 14px 16px 8px; }
.rank-head h3 { font-size: 17px; font-weight: 700; }
.sub { font-size: 12px; color: #999; }

.rank-table { margin: 0 12px; background: #fff; border-radius: 12px; overflow: hidden; box-shadow: 0 1px 4px rgba(0,0,0,.04); }
.rank-row {
  display: flex; align-items: center; padding: 12px 14px;
  border-bottom: 1px solid #f5f5f5;
}
.rank-row:last-child { border-bottom: none; }
.rank-row.active { background: #e8f5e9; }
.rank-row.dropped { opacity: .45; background: #fafafa; }

.col-rank { width: 22px; font-weight: 700; font-size: 15px; color: #333; text-align: center; flex-shrink: 0; }
.col-player { flex: 1.5; display: flex; flex-direction: column; align-items: center; gap: 2px; }
.p-name { font-size: 12px; color: #333; max-width: 64px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.col-wl { flex: 1; text-align: center; font-size: 15px; color: #666; letter-spacing: 2px; }
.wl-win { font-style: normal; color: #e74c3c; font-weight: 600; margin-right: 2px; }
.col-diff { flex: 1; text-align: center; font-size: 15px; font-weight: 600; }
.positive { color: #07c160; }
.negative { color: #e74c3c; }
.col-rate { flex: 1; text-align: center; font-weight: 700; font-size: 17px; color: #333; }
</style>
