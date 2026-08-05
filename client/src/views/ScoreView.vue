<template>
  <div class="score-root">
    <van-nav-bar :title="matchNum" left-text="返回" left-arrow @click-left="goBack">
      <template #right>
        <span v-if="timerActive" class="elapsed">{{ fmtElapsed(elapsed) }}</span>
      </template>
    </van-nav-bar>

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
            <span class="sb-big" :class="{ red: leftScore > rightScore }">{{ leftScore }}</span>
            <span class="sb-colon">:</span>
            <span class="sb-big" :class="{ red: rightScore > leftScore }">{{ rightScore }}</span>
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

      <div class="support-bar" v-if="match">
        <div class="support-track">
          <div class="support-inner">
            <div class="support-fill a" :style="{ flex: supportA }" @click.stop="doSupport('a')">
              <div class="support-avatars">
                <img v-for="(av, i) in (swapped ? match.support_b_users : match.support_a_users)" :key="'a'+i" :src="av || defaultAvatar" class="support-av" :style="{ zIndex: (swapped ? match.support_b_users.length : match.support_a_users.length) - i }" />
              </div>
            </div>
            <div class="support-fill b" :style="{ flex: supportB }" @click.stop="doSupport('b')">
              <div class="support-avatars">
                <img v-for="(av, i) in (swapped ? match.support_a_users : match.support_b_users)" :key="'b'+i" :src="av || defaultAvatar" class="support-av" :style="{ zIndex: (swapped ? match.support_a_users.length : match.support_b_users.length) - i }" />
              </div>
            </div>
          </div>
        </div>
        <div class="support-labels">
          <span class="support-label" :class="{ active: match.my_support === (swapped ? 'b' : 'a') }" @click="doSupport('a')">🔥 {{ swapped ? (match.support_b || 0) : (match.support_a || 0) }} 票</span>
          <span class="support-label" :class="{ active: match.my_support === (swapped ? 'a' : 'b') }" @click="doSupport('b')">🔥 {{ swapped ? (match.support_a || 0) : (match.support_b || 0) }} 票</span>
        </div>
        <div class="support-hint" v-if="canSupport && match.status !== 'finished'">点击支持你喜欢的队伍</div>
      </div>

      <div v-if="isReferee" class="controls">
        <div class="wheel-board">
          <div class="wheel-side">
            <button class="wheel-btn plus" :disabled="match.status === 'finished'" @click="addScore(swapSide('a'))">+</button>
            <div
              class="wheel"
              :class="{ dragging: wheelSide === 'a' }"
              @pointerdown="wheelDown($event, 'a')"
              @pointermove="wheelMove($event)"
              @pointerup="wheelUp($event)"
              @pointercancel="wheelUp($event)"
            >
              <span class="wheel-item top" :class="{ empty: leftScore === 0 }">{{ leftScore > 0 ? leftScore - 1 : '' }}</span>
              <span class="wheel-item mid">{{ leftScore }}</span>
              <span class="wheel-item bot">{{ leftScore + 1 }}</span>
            </div>
            <button class="wheel-btn minus" :disabled="match.status === 'finished' || leftScore <= 0" @click="subScore(swapSide('a'))">-</button>
          </div>

          <div class="wheel-colon">:</div>

          <div class="wheel-side">
            <button class="wheel-btn plus" :disabled="match.status === 'finished'" @click="addScore(swapSide('b'))">+</button>
            <div
              class="wheel"
              :class="{ dragging: wheelSide === 'b' }"
              @pointerdown="wheelDown($event, 'b')"
              @pointermove="wheelMove($event)"
              @pointerup="wheelUp($event)"
              @pointercancel="wheelUp($event)"
            >
              <span class="wheel-item top" :class="{ empty: rightScore === 0 }">{{ rightScore > 0 ? rightScore - 1 : '' }}</span>
              <span class="wheel-item mid">{{ rightScore }}</span>
              <span class="wheel-item bot">{{ rightScore + 1 }}</span>
            </div>
            <button class="wheel-btn minus" :disabled="match.status === 'finished' || rightScore <= 0" @click="subScore(swapSide('b'))">-</button>
          </div>
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
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { useWebSocket } from '@/composables/useWebSocket'
import { useGoBack } from '@/composables/useGoBack'
import api from '@/api/client'
import { showToast, showConfirmDialog } from 'vant'

const route = useRoute()
const { goBack } = useGoBack()
const matchNum = computed(() => route.query.num ? `第${route.query.num}场` : '记分')
const auth = useAuthStore()
const match = ref<any>(null)
const swapped = ref(false)
const defaultAvatar = 'https://img.yzcdn.cn/vant/cat.jpeg'

// --- 比赛持续时长（纯前端，localStorage 持久化） ---
const elapsed = ref(0)
const timerActive = ref(false)
let timer: ReturnType<typeof setInterval> | null = null
const storageKey = computed(() => `score_start_${route.params.matchId}`)
const MAX_MATCH_SECONDS = 3 * 60 * 60

function fmtElapsed(sec: number) {
  const h = Math.floor(sec / 3600)
  const m = Math.floor((sec % 3600) / 60)
  const s = sec % 60
  const hh = h.toString().padStart(2, '0')
  const mm = m.toString().padStart(2, '0')
  const ss = s.toString().padStart(2, '0')
  return h > 0 ? `${hh}:${mm}:${ss}` : `${mm}:${ss}`
}

function updateElapsed() {
  const start = Number(localStorage.getItem(storageKey.value))
  if (!start) return
  const secs = Math.max(0, Math.floor((Date.now() - start) / 1000))
  // 超过上限视为过期（如忘记结束、隔天补录），清除计时等待下次记分重新开始
  if (secs > MAX_MATCH_SECONDS) {
    stopTimer(true)
    return
  }
  elapsed.value = secs
}

function startTimerIfNeeded() {
  if (timerActive.value || match.value?.status === 'finished') return
  const stored = localStorage.getItem(storageKey.value)
  const start = stored ? Number(stored) : 0
  if (!start || Date.now() - start > MAX_MATCH_SECONDS * 1000) {
    localStorage.setItem(storageKey.value, String(Date.now()))
  }
  timerActive.value = true
  updateElapsed()
  timer = setInterval(updateElapsed, 1000)
}

function stopTimer(clearStorage: boolean) {
  if (timer) { clearInterval(timer); timer = null }
  timerActive.value = false
  if (clearStorage) localStorage.removeItem(storageKey.value)
}
// --- 时长逻辑结束 ---

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
const canSupport = computed(() => {
  if (!match.value || !auth.user) return false
  const players = [
    match.value.pairing_a?.player_a?.id, match.value.pairing_a?.player_b?.id,
    match.value.pairing_b?.player_a?.id, match.value.pairing_b?.player_b?.id,
  ]
  if (players.includes(auth.user.id)) return false
  if (match.value.referee?.id === auth.user.id) return false
  return true
})
const supportA = computed(() => {
  const a = match.value?.support_a || 0
  const b = match.value?.support_b || 0
  if (a + b === 0) return 1
  return swapped.value ? b : a
})
const supportB = computed(() => {
  const a = match.value?.support_a || 0
  const b = match.value?.support_b || 0
  if (a + b === 0) return 1
  return swapped.value ? a : b
})
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
  const res = await api.get(`/tournaments/${route.params.id}/matches/${route.params.matchId}`, { skipLoading: true } as any)
  match.value = res.data
}

async function doSupport(side: string) {
  if (!canSupport.value || match.value?.status === 'finished') return
  const actualSide = swapped.value ? (side === 'a' ? 'b' : 'a') : side
  try {
    const res = await api.post(`/tournaments/${route.params.id}/matches/${route.params.matchId}/support`, { side: actualSide }, { skipLoading: true } as any)
    match.value.support_a = res.data.support_a
    match.value.support_b = res.data.support_b
    match.value.my_support = actualSide
  } catch {}
}

let scoreTimer: ReturnType<typeof setTimeout> | null = null
let pendingScore: { score_a: number; score_b: number } | null = null
const wheelSide = ref<string | null>(null)
let wheelGesture: { id: number; y: number; acc: number; side: string } | null = null

async function flushScoreNow() {
  if (!pendingScore || !match.value) return
  const data = pendingScore
  pendingScore = null
  await api.put(`/tournaments/${route.params.id}/matches/${route.params.matchId}/score`, data, { skipLoading: true } as any).catch(() => {})
  // await 期间用户又点击了，需要继续冲刷，避免丢分
  if (pendingScore) scheduleFlush()
}

function flushScore() {
  flushScoreNow()
}

function scheduleFlush() {
  if (scoreTimer) clearTimeout(scoreTimer)
  scoreTimer = setTimeout(flushScore, 300)
}

async function addScore(side: string) {
  if (!match.value) return
  startTimerIfNeeded()
  if (side === 'a') match.value.score_a = (match.value.score_a ?? 0) + 1
  else match.value.score_b = (match.value.score_b ?? 0) + 1
  pendingScore = { score_a: match.value.score_a ?? 0, score_b: match.value.score_b ?? 0 }
  scheduleFlush()
}

async function subScore(side: string) {
  if (!match.value) return
  startTimerIfNeeded()
  if (side === 'a' && (match.value.score_a ?? 0) > 0) match.value.score_a -= 1
  else if (side === 'b' && (match.value.score_b ?? 0) > 0) match.value.score_b -= 1
  else return
  pendingScore = { score_a: match.value.score_a ?? 0, score_b: match.value.score_b ?? 0 }
  scheduleFlush()
}

function wheelDown(e: PointerEvent, side: string) {
  if (match.value?.status === 'finished') return
  ;(e.currentTarget as HTMLElement).setPointerCapture?.(e.pointerId)
  wheelGesture = { id: e.pointerId, y: e.clientY, acc: 0, side }
  wheelSide.value = side
}

function wheelMove(e: PointerEvent) {
  const g = wheelGesture
  if (!g || e.pointerId !== g.id) return
  // 上滑 clientY 变小，dy 为正代表加分
  const dy = g.y - e.clientY
  g.y = e.clientY
  g.acc += dy
  const step = 26
  while (g.acc >= step) {
    addScore(swapSide(g.side))
    g.acc -= step
  }
  while (g.acc <= -step) {
    subScore(swapSide(g.side))
    g.acc += step
  }
}

function wheelUp(e: PointerEvent) {
  const g = wheelGesture
  if (!g || e.pointerId !== g.id) return
  wheelGesture = null
  wheelSide.value = null
}

async function endMatch() {
  try { await showConfirmDialog({ title: '确认结束', message: '确定要结束本场比赛吗？结束后无法恢复。' }) } catch { return }
  if (scoreTimer) { clearTimeout(scoreTimer); scoreTimer = null }
  if (pendingScore) { await flushScoreNow() }
  await api.put(`/tournaments/${route.params.id}/matches/${route.params.matchId}/score`, {
    score_a: match.value.score_a ?? 0, score_b: match.value.score_b ?? 0, force_end: true
  })
  stopTimer(true)
  showToast('比赛结束')
  goBack()
}

async function claimReferee() {
  await api.post(`/tournaments/${route.params.id}/matches/${route.params.matchId}/claim-referee`)
  showToast('认领成功')
  await fetchMatch()
}

const tid = Number(route.params.id)
const { lastMessage } = useWebSocket(tid)
watch(lastMessage, (msg) => {
  if (!msg) return
  if (msg.type === 'match_updated' && msg.match_id === Number(route.params.matchId)) {
    if (!pendingScore) {
      if (msg.score_a != null) match.value.score_a = msg.score_a
      if (msg.score_b != null) match.value.score_b = msg.score_b
    }
    if (msg.status) match.value.status = msg.status
    if (msg.status === 'finished') stopTimer(true)
  }
  if (msg.type === 'support_updated' && msg.match_id === Number(route.params.matchId)) {
    match.value.support_a = msg.support_a
    match.value.support_b = msg.support_b
    fetchMatch()
  }
})

onMounted(async () => {
  await Promise.all([auth.fetchMe(), fetchMatch()])
  if (match.value?.status === 'finished') {
    stopTimer(true)
  } else if (localStorage.getItem(storageKey.value)) {
    startTimerIfNeeded()
  }
})

onUnmounted(() => stopTimer(false))
</script>

<style scoped>
.score-root { min-height: 100vh; background: #f0f2f5; padding-bottom: 40px; }
.loading { display: flex; justify-content: center; margin-top: 120px; }
.elapsed { font-size: 12px; color: #666; margin-right: 4px; font-variant-numeric: tabular-nums; }

.scoreboard { display: flex; align-items: center; padding: 16px 8px; background: #fff; margin: 10px 12px; border-radius: 14px; box-shadow: 0 2px 12px rgba(0,0,0,.06); }
.sb-team { flex: 1; display: flex; justify-content: center; gap: 4px; cursor: pointer; }
.sb-player { display: flex; flex-direction: column; align-items: center; gap: 3px; font-size: 12px; color: #666; }
.sb-team.win .sb-player span { color: #07c160; font-weight: 600; }

.sb-center { width: 120px; text-align: center; cursor: pointer; }
.sb-score-row { display: flex; align-items: center; justify-content: center; gap: 8px; margin-bottom: 8px; }
.sb-big { font-size: 48px; font-weight: 900; line-height: 1; font-variant-numeric: tabular-nums; color: #222; }
.sb-big.red { color: #e74c3c; }
.sb-colon { font-size: 36px; color: #ccc; }
.sb-status { margin-bottom: 6px; }
.sb-info { font-size: 11px; color: #999; display: flex; flex-direction: column; gap: 2px; }
.swap-hint { margin-top: 4px; font-size: 10px; color: #ccc; }

.controls { padding: 20px 20px 40px; }

.wheel-board { display: flex; align-items: center; justify-content: space-between; margin-bottom: 20px; padding: 18px 12px; background: #fff; border-radius: 16px; box-shadow: 0 2px 12px rgba(0,0,0,.06); }
.wheel-side { display: flex; flex-direction: column; align-items: center; gap: 10px; }
.wheel-btn { width: 42px; height: 42px; border-radius: 50%; border: none; font-size: 24px; font-weight: 600; cursor: pointer; display: flex; align-items: center; justify-content: center; transition: transform .1s, opacity .1s; color: #fff; }
.wheel-btn:active { transform: scale(.92); }
.wheel-btn.plus { background: #ff9800; box-shadow: 0 2px 8px rgba(255,152,0,.3); }
.wheel-btn.minus { background: #eee; color: #666; }
.wheel-btn:disabled { opacity: .3; }
.wheel { width: 88px; height: 128px; position: relative; overflow: hidden; touch-action: none; user-select: none; }
.wheel.dragging { background: rgba(25,137,250,.08); box-shadow: inset 0 0 0 3px rgba(25,137,250,.12); }
.wheel-item { position: absolute; left: 0; right: 0; text-align: center; line-height: 1; font-variant-numeric: tabular-nums; transition: transform .2s, opacity .2s; }
.wheel-item.top { top: 8px; font-size: 24px; color: #c8c9cc; }
.wheel-item.mid { top: 50%; transform: translateY(-50%); font-size: 34px; font-weight: 700; color: #323233; }
.wheel-item.bot { bottom: 8px; font-size: 24px; color: #c8c9cc; }
.wheel-item.empty { opacity: 0; }
.wheel-colon { font-size: 44px; font-weight: 600; color: #969799; }

.support-bar { margin: 0 12px 8px; background: #fff; border-radius: 14px; padding: 12px 16px; box-shadow: 0 2px 12px rgba(0,0,0,.06); }
.support-track { height: 32px; border-radius: 16px; overflow: hidden; background: #f0f0f0; }
.support-fill.a { background: #1989fa; transition: flex .3s; cursor: pointer; display: flex; align-items: center; justify-content: flex-end; padding-right: 8px; min-width: 0; overflow: hidden; }
.support-fill.b { background: #e74c3c; transition: flex .3s; cursor: pointer; display: flex; align-items: center; padding-left: 8px; min-width: 0; overflow: hidden; }
.support-inner { display: flex; height: 100%; }
.support-divider {
  position: absolute; top: 50%; transform: translate(-50%, -50%);
  font-size: 26px;  pointer-events: none; line-height: 1;
  color: #ffd700;
  text-shadow: 0 0 8px #ffd700, 0 0 2px #fff;
  filter: drop-shadow(0 0 2px rgba(0,0,0,.3));
  animation: flicker 1.5s ease-in-out infinite alternate;
}
@keyframes flicker {
  0% { transform: translate(-50%, -50%) scale(1); }
  100% { transform: translate(-50%, -50%) scale(1.2); }
}
.support-avatars { display: flex; align-items: center; gap: 1px; flex-shrink: 0; }
.support-av { width: 22px; height: 22px; border-radius: 50%; border: 1.5px solid #fff; object-fit: cover; margin-left: -6px; }
.support-av:first-child { margin-left: 0; }
.support-labels { display: flex; justify-content: space-between; margin-top: 6px; }
.support-label { font-size: 13px; color: #999; cursor: pointer; user-select: none; }
.support-label .flame { display: inline-block; animation: flicker 1.5s ease-in-out infinite alternate; }
.support-label.active { color: #1989fa; font-weight: 600; }
.support-hint { text-align: center; font-size: 10px; color: #ccc; margin-top: 4px; }

.no-role { padding: 60px 20px; text-align: center; }
.no-role p { color: #999; font-size: 14px; margin-top: 16px; }
</style>
