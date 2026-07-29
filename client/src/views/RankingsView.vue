<template>
  <div class="rank-page">
    <van-nav-bar title="积分榜" left-text="返回" left-arrow @click-left="$router.back()" />

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
          <span class="col-rank">{{ p.is_active ? p.rank : '-' }}</span>
          <div class="col-player">
            <van-image round width="36" height="36" :src="p.avatar || defaultAvatar" />
            <span class="p-name">{{ p.username }}</span>
          </div>
          <div class="col-stats">
            <span>{{ p.matches_won }}胜{{ p.matches_lost }}负</span>
            <em :class="p.point_diff >= 0 ? 'positive' : 'negative'">{{ p.point_diff > 0 ? '+' : '' }}{{ p.point_diff }}</em>
          </div>
          <span class="col-rate">{{ winRate(p) }}%</span>
        </div>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import api from '@/api/client'

const route = useRoute()
const auth = useAuthStore()
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

onMounted(async () => {
  await auth.fetchMe()
  await fetchRankings()
})
</script>

<style scoped>
.rank-page { height: 100vh; overflow-y: auto; background: #f0f2f5; padding-bottom: 60px; }
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

.col-rank { width: 28px; font-weight: 700; font-size: 16px; color: #333; }
.col-player { flex: 1; display: flex; flex-direction: column; align-items: center; gap: 3px; }
.p-name { font-size: 12px; color: #333; max-width: 64px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.col-stats { width: 90px; text-align: center; font-size: 13px; color: #666; display: flex; flex-direction: column; gap: 2px; }
.col-stats em { font-style: normal; font-size: 11px; }
.positive { color: #07c160; }
.negative { color: #e74c3c; }
.col-rate { width: 40px; text-align: right; font-weight: 700; font-size: 15px; color: #333; }
</style>
