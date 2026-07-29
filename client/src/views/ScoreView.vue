<template>
  <div class="score-root">
    <van-nav-bar title="记分" left-text="返回" left-arrow @click-left="$router.back()" />

    <van-loading v-if="!match" class="loading" />
    <template v-else>
      <div class="scoreboard">
        <div class="sb-team" :class="{ win: leftWin }" @click="swapTeams">
          <div class="sb-player">
            <van-image round width="40" height="40" :src="leftTeam.a.avatar || defaultAvatar" />
            <span>{{ leftTeam.a.username }}</span>
          </div>
          <div class="sb-player">
            <van-image round width="40" height="40" :src="leftTeam.b.avatar || defaultAvatar" />
            <span>{{ leftTeam.b.username }}</span>
          </div>
        </div>

        <div class="sb-center" @click="swapTeams">
          <div class="sb-score-row">
            <span class="sb-big">{{ leftScore }}</span>
            <span class="sb-colon">:</span>
            <span class="sb-big">{{ rightScore }}</span>
          </div>
          <div class="sb-status">
            <van-tag :type="match.status === 'finished' ? 'success' : 'warning'" size="medium" round>
              {{ match.status === 'finished' ? '已结束' : '进行中' }}
            </van-tag>
          </div>
          <div class="sb-info">
            <span v-if="match.court_name">{{ match.court_name }}</span>
            <span v-if="match.referee">裁判 {{ match.referee.username }}</span>
          </div>
          <div class="swap-hint">点击交换场地</div>
        </div>

        <div class="sb-team" :class="{ win: rightWin }" @click="swapTeams">
          <div class="sb-player">
            <van-image round width="40" height="40" :src="rightTeam.a.avatar || defaultAvatar" />
            <span>{{ rightTeam.a.username }}</span>
          </div>
          <div class="sb-player">
            <van-image round width="40" height="40" :src="rightTeam.b.avatar || defaultAvatar" />
            <span>{{ rightTeam.b.username }}</span>
          </div>
        </div>
      </div>

      <div v-if="isReferee" class="controls">
        <div class="ctrl-row">
          <button class="ctrl-btn plus" :disabled="match.status === 'finished'" @click="addScore(swapSide('a'))">+1</button>
          <span class="ctrl-label">左队加分</span>
          <span class="ctrl-label">右队加分</span>
          <button class="ctrl-btn plus" :disabled="match.status === 'finished'" @click="addScore(swapSide('b'))">+1</button>
        </div>
        <div class="ctrl-row">
          <button class="ctrl-btn minus" :disabled="match.status === 'finished' || leftScore <= 0" @click="subScore(swapSide('a'))" >-1</button>
          <span class="ctrl-hint">减分</span>
          <span class="ctrl-hint">减分</span>
          <button class="ctrl-btn minus" :disabled="match.status === 'finished' || rightScore <= 0" @click="subScore(swapSide('b'))" >-1</button>
        </div>
        <van-button type="danger" block round size="large" style="margin-top:20px" @click="endMatch" :disabled="match.status === 'finished'">结束比赛</van-button>
      </div>

      <div v-else-if="match.can_referee" class="no-role">
        <van-button type="primary" block round size="large" @click="claimReferee">申请成为裁判</van-button>
      </div>

      <div v-else class="no-role">
        <p>暂无裁判权限</p>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { useWebSocket } from '@/composables/useWebSocket'
import api from '@/api/client'
import { showToast } from 'vant'

const route = useRoute()
const auth = useAuthStore()
const match = ref<any>(null)
const swapped = ref(false)
const defaultAvatar = 'https://img.yzcdn.cn/vant/cat.jpeg'

const isReferee = computed(() => match.value?.referee?.id === auth.user?.id)

function pp(path: string) {
  const parts = path.split('.')
  let obj: any = match.value
  for (const k of parts) obj = obj?.[k]
  return obj || {}
}

const leftTeam = computed(() => {
  if (swapped.value) return { a: pp('pairing_b.player_a'), b: pp('pairing_b.player_b') }
  return { a: pp('pairing_a.player_a'), b: pp('pairing_a.player_b') }
})
const rightTeam = computed(() => {
  if (swapped.value) return { a: pp('pairing_a.player_a'), b: pp('pairing_a.player_b') }
  return { a: pp('pairing_b.player_a'), b: pp('pairing_b.player_b') }
})
const leftScore = computed(() => swapped.value ? (match.value?.score_b ?? 0) : (match.value?.score_a ?? 0))
const rightScore = computed(() => swapped.value ? (match.value?.score_a ?? 0) : (match.value?.score_b ?? 0))
const leftWin = computed(() => {
  const w = match.value?.winner_pairing_id
  if (!w) return false
  return swapped.value ? w === match.value?.pairing_b?.id : w === match.value?.pairing_a?.id
})
const rightWin = computed(() => {
  const w = match.value?.winner_pairing_id
  if (!w) return false
  return swapped.value ? w === match.value?.pairing_a?.id : w === match.value?.pairing_b?.id
})

function swapTeams() { swapped.value = !swapped.value }
function swapSide(side: string) { return (!swapped.value) ? side : (side === 'a' ? 'b' : 'a') }

async function fetchMatch() {
  const res = await api.get(`/tournaments/${route.params.id}/matches/${route.params.matchId}`)
  match.value = res.data
}

async function addScore(side: string) {
  if (!match.value) return
  if (side === 'a') match.value.score_a = (match.value.score_a ?? 0) + 1
  else match.value.score_b = (match.value.score_b ?? 0) + 1
  await api.put(`/tournaments/${route.params.id}/matches/${route.params.matchId}/score`, { score_a: match.value.score_a, score_b: match.value.score_b })
}

async function subScore(side: string) {
  if (!match.value) return
  if (side === 'a' && (match.value.score_a ?? 0) > 0) match.value.score_a -= 1
  else if (side === 'b' && (match.value.score_b ?? 0) > 0) match.value.score_b -= 1
  else return
  await api.put(`/tournaments/${route.params.id}/matches/${route.params.matchId}/score`, { score_a: match.value.score_a, score_b: match.value.score_b })
}

async function endMatch() {
  await api.put(`/tournaments/${route.params.id}/matches/${route.params.matchId}/score`, {
    score_a: match.value.score_a ?? 0, score_b: match.value.score_b ?? 0, force_end: true
  })
  showToast('比赛结束')
  await fetchMatch()
}

async function claimReferee() {
  await api.post(`/tournaments/${route.params.id}/matches/${route.params.matchId}/claim-referee`)
  showToast('认领成功')
  await fetchMatch()
}

const tid = Number(route.params.id)
const { lastMessage } = useWebSocket(tid)
watch(lastMessage, (msg) => { if (msg?.type === 'match_updated' && msg.match_id === Number(route.params.matchId)) fetchMatch() })

onMounted(async () => { await auth.fetchMe(); await fetchMatch() })
</script>

<style scoped>
.score-root { min-height: 100vh; height: 100vh; overflow: hidden; position: fixed; inset: 0; z-index: 1; background: linear-gradient(180deg, #0d1b2a 0%, #1b2838 50%, #0d1b2a 100%); color: #fff; }
.loading { display: flex; justify-content: center; margin-top: 120px; }

.scoreboard { display: flex; align-items: center; padding: 20px 8px 16px; }
.sb-team { flex: 1; display: flex; justify-content: center; gap: 4px; cursor: pointer; }
.sb-player { display: flex; flex-direction: column; align-items: center; gap: 3px; font-size: 12px; color: #ccc; }
.sb-team.win .sb-player span { color: #ffd700; font-weight: 600; }

.sb-center { width: 130px; text-align: center; cursor: pointer; }
.sb-score-row { display: flex; align-items: center; justify-content: center; gap: 6px; margin-bottom: 8px; }
.sb-big { font-size: 52px; font-weight: 900; line-height: 1; font-variant-numeric: tabular-nums; color: #fff; text-shadow: 0 0 20px rgba(255,255,255,.3); }
.sb-colon { font-size: 40px; color: rgba(255,255,255,.3); }
.sb-status { margin-bottom: 6px; }
.sb-info { font-size: 11px; color: #8899aa; display: flex; flex-direction: column; gap: 2px; }
.swap-hint { margin-top: 6px; font-size: 10px; color: #445566; }

.controls { padding: 20px 20px 40px; }
.ctrl-row { display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; }
.ctrl-btn { width: 56px; height: 56px; border-radius: 50%; border: none; font-size: 22px; font-weight: 800; cursor: pointer; display: flex; align-items: center; justify-content: center; transition: transform .1s, opacity .1s; }
.ctrl-btn:active { transform: scale(.92); }
.ctrl-btn.plus { background: #07c160; color: #fff; }
.ctrl-btn.minus { background: rgba(255,255,255,.12); color: #fff; }
.ctrl-btn:disabled { opacity: .25; }
.ctrl-label { font-size: 13px; color: #8899aa; }
.ctrl-hint { font-size: 11px; color: #556677; }

.no-role { padding: 60px 20px; text-align: center; }
.no-role p { color: #667788; font-size: 14px; margin-top: 16px; }
</style>
