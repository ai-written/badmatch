<template>
  <div class="schedule-page">
    <van-nav-bar title="对阵表" left-text="返回" left-arrow @click-left="goBack" />

    <div class="schedule-scroll">
    <van-pull-refresh v-model="refreshing" @refresh="onRefresh" class="pull-fill">
      <div class="pull-inner">
    <div v-if="rounds.length === 0" class="empty-block">
      <van-empty description="暂无赛程" />
    </div>

    <template v-else>
      <div v-for="r in flatRounds" :key="r.id" class="round-section">


        <div v-for="m in r.matches" :key="m.id" class="match-card" :class="{ my: isMyMatch(m) }" @click="goScore(m)">
          <div class="match-row">
            <div class="match-side" :class="{ win: m.winner_pairing_id === m.pairing_a.id }">
              <div class="player-block" :class="{ win: m.winner_pairing_id === m.pairing_a.id }">
                <div class="avatar-badge">
                  <van-image round width="36" height="36" :src="m.pairing_a.player_a.avatar || defaultAvatar" />
                  <span v-if="m.pairing_a.player_a.id === auth.user?.id" class="me-badge">我</span>
                  <span class="badge-icon" v-if="m.status === 'finished' && m.winner_pairing_id === m.pairing_a.id">🏆</span>
                </div>
                <span class="player-name">{{ m.pairing_a.player_a.username }}</span>
              </div>
              <div class="player-block" :class="{ win: m.winner_pairing_id === m.pairing_a.id }">
                <div class="avatar-badge">
                  <van-image round width="36" height="36" :src="m.pairing_a.player_b.avatar || defaultAvatar" />
                  <span v-if="m.pairing_a.player_b.id === auth.user?.id" class="me-badge">我</span>
                  <span class="badge-icon" v-if="m.status === 'finished' && m.winner_pairing_id === m.pairing_a.id">🏆</span>
                </div>
                <span class="player-name">{{ m.pairing_a.player_b.username }}</span>
              </div>
            </div>

            <div class="match-mid">
              <template v-if="m.status === 'finished'">
                <span class="score-num" :class="{ red: m.score_a > m.score_b }">{{ m.score_a }}</span>
                <span class="score-div">:</span>
                <span class="score-num" :class="{ red: m.score_b > m.score_a }">{{ m.score_b }}</span>
              </template>
              <template v-else-if="m.score_a != null || m.score_b != null">
                <span class="score-num">{{ m.score_a }}</span>
                <span class="score-div">:</span>
                <span class="score-num">{{ m.score_b }}</span>
              </template>
              <span v-else class="vs-badge">VS</span>
            </div>

            <div class="match-side" :class="{ win: m.winner_pairing_id === m.pairing_b.id }">
              <div class="player-block" :class="{ win: m.winner_pairing_id === m.pairing_b.id }">
                <div class="avatar-badge">
                  <van-image round width="36" height="36" :src="m.pairing_b.player_a.avatar || defaultAvatar" />
                  <span v-if="m.pairing_b.player_a.id === auth.user?.id" class="me-badge">我</span>
                  <span class="badge-icon" v-if="m.status === 'finished' && m.winner_pairing_id === m.pairing_b.id">🏆</span>
                </div>
                <span class="player-name">{{ m.pairing_b.player_a.username }}</span>
              </div>
              <div class="player-block" :class="{ win: m.winner_pairing_id === m.pairing_b.id }">
                <div class="avatar-badge">
                  <van-image round width="36" height="36" :src="m.pairing_b.player_b.avatar || defaultAvatar" />
                  <span v-if="m.pairing_b.player_b.id === auth.user?.id" class="me-badge">我</span>
                  <span class="badge-icon" v-if="m.status === 'finished' && m.winner_pairing_id === m.pairing_b.id">🏆</span>
                </div>
                <span class="player-name">{{ m.pairing_b.player_b.username }}</span>
              </div>
            </div>

            <div class="match-info">
              <span class="match-num">第{{ m.globalIdx }}场</span>
              <span v-if="m.status === 'finished' && m.duration_seconds != null" class="match-dur">{{ fmtDuration(m.duration_seconds) }}</span>
              <span v-if="m.referee" class="foot-ref has">裁 {{ m.referee.username }}</span>
              <van-button v-if="m.can_referee" size="mini" type="primary" round @click.stop="claimReferee(m.id)">裁判</van-button>
            </div>
          </div>
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
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import api from '@/api/client'
import { useWebSocket } from '@/composables/useWebSocket'
import { useGoBack } from '@/composables/useGoBack'
import { showToast } from 'vant'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const { goBack } = useGoBack()
const refreshing = ref(false)
const rounds = ref<any[]>([])
const defaultAvatar = 'https://img.yzcdn.cn/vant/cat.jpeg'

function isMyMatch(m: any) {
  if (!auth.user) return false
  const uid = auth.user.id
  return [m.pairing_a?.player_a?.id, m.pairing_a?.player_b?.id, m.pairing_b?.player_a?.id, m.pairing_b?.player_b?.id].includes(uid)
}

function fmtDuration(sec: number) {
  if (sec < 60) return `${sec}秒`
  return `${Math.round(sec / 60)}分钟`
}

async function fetchRounds() {
  const res = await api.get(`/tournaments/${route.params.id}/rounds`)
  rounds.value = res.data
}

function goScore(m: any) {
  if (!auth.token) {
    sessionStorage.setItem('loginRedirect', `/tournament/${route.params.id}/schedule`)
    router.push('/profile')
    return
  }
  router.push(`/tournament/${route.params.id}/score/${m.id}?num=${m.globalIdx}`)
}

async function claimReferee(matchId: number) {
  await api.post(`/tournaments/${route.params.id}/matches/${matchId}/claim-referee`)
  showToast('认领成功')
  await fetchRounds()
}

const tid = Number(route.params.id)
const { lastMessage } = useWebSocket(tid)
watch(lastMessage, (msg) => { if (msg?.type === 'match_updated') fetchRounds() })
async function onRefresh() {
  try { await fetchRounds() } finally { refreshing.value = false }
}
const flatRounds = computed(() => {
  let idx = 0
  return rounds.value.map(r => ({
    ...r,
    matches: r.matches.map((m: any) => ({ ...m, globalIdx: ++idx }))
  }))
})
onMounted(() => fetchRounds())
</script>

<style scoped>
.schedule-page { height: 100vh; display: flex; flex-direction: column; background: #f0f2f5; }
.schedule-scroll { flex: 1; overflow-y: auto; }
.pull-fill { min-height: 100%; }
.pull-inner { padding-bottom: 60px; }
.empty-block { padding-top: 80px; }
.round-section { margin: 0 12px; }


.match-card {
  background: #fff; border-radius: 12px; padding: 12px 14px; margin-bottom: 10px;
  box-shadow: 0 2px 8px rgba(0,0,0,.04); position: relative;
}

.match-card:active { box-shadow: 0 4px 12px rgba(0,0,0,.08); }
.me-badge {
  position: absolute; top: -2px; left: -2px;
  font-size: 8px; color: #fff; background: #1989fa;
  padding: 0 3px; border-radius: 6px; line-height: 14px;
  z-index: 1;
}

.match-row { display: flex; align-items: center; }
.match-side { flex: 1; display: flex; justify-content: center; gap: 8px; }
.player-block { display: flex; flex-direction: column; align-items: center; gap: 3px; }
.player-block.win .player-name { color: #07c160; font-weight: 600; }
.player-name { font-size: 12px; color: #333; max-width: 52px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; text-align: center; }

.match-mid {
  width: 64px; text-align: center; flex-shrink: 0;
  display: flex; align-items: center; justify-content: center;
}
.score-num { font-size: 20px; font-weight: 800; color: #222; font-variant-numeric: tabular-nums; }
.score-num.red { color: #e74c3c; }
.score-div { font-size: 16px; color: #ccc; margin: 0 2px; }

.match-info {
  width: 52px; flex-shrink: 0; margin-left: 4px;
  display: flex; flex-direction: column; align-items: center; gap: 3px;
}
.match-num { font-size: 10px; color: #aaa; }
.match-dur { font-size: 10px; color: #bbb; }
.match-info .van-button { font-size: 10px; height: 22px; padding: 0 6px; }

.avatar-badge { position: relative; display: inline-block; }
.badge-icon {
  position: absolute; top: -4px; right: -4px;
  font-size: 14px; line-height: 1;
  filter: drop-shadow(0 1px 2px rgba(0,0,0,.3));
}

.vs-badge { display: inline-block; padding: 3px 10px; border-radius: 10px; font-size: 12px; font-weight: 700; color: #999; background: #f5f5f5; }

.score-foot { margin-top: 6px; display: flex; flex-direction: column; align-items: center; gap: 4px; }
.score-foot .van-button { margin-top: 2px; }
.foot-ref.has { color: #07c160; font-weight: 500; font-size: 11px; }
</style>
