<template>
  <div class="score-root vh-page-scroll">
    <van-nav-bar left-text="返回" left-arrow @click-left="goBack">
      <!-- 中间：场次 + 耗时；右侧：积分榜入口 -->
      <template #title>
        <span class="nav-title" :class="{ overdue: timerOverdue }">{{ matchNum }}<template v-if="timerText">（{{ timerText }}）</template></span>
      </template>
      <template #right>
        <span class="nav-rank" @click.stop="$router.push(`/tournament/${route.params.id}/rankings`)">积分榜</span>
      </template>
    </van-nav-bar>

    <van-loading v-if="!match && !loadFailed" class="loading" />
    <div v-else-if="!match && loadFailed" class="load-failed">
      <van-icon name="warning-o" size="24" />
      <p>比赛加载失败</p>
      <p class="load-failed-sub">比赛可能已被删除，或网络异常</p>
      <van-button size="small" round plain type="primary" @click="goBack">返回</van-button>
    </div>
    <template v-else>
      <div v-if="locked" class="readonly-banner">
        <van-icon name="lock" />
        <span>赛事已结束，本场为只读状态，不能再记分或改动数据</span>
      </div>
      <div class="scoreboard">
        <div class="sb-team" :class="{ win: leftWin }" @click="swapTeams">
          <div class="sb-player">
            <van-image lazy-load round width="40" height="40" :src="leftTeam.a.avatar || defaultAvatar" />
            <span>{{ leftTeam.a.username }}</span>
          </div>
          <div class="sb-player">
            <van-image lazy-load round width="40" height="40" :src="leftTeam.b.avatar || defaultAvatar" />
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
            <span v-if="match.referee">裁判 {{ match.referee.username }}<template v-if="!match.active_referee">（已卸任）</template></span>
          </div>
          <div class="swap-hint">{{ canOperate ? '点击交换场地' : (locked ? '赛事已结束，本场已锁定' : (match.status === 'finished' ? '比赛已结束' : '仅裁判可交换场地')) }}</div>
        </div>

        <div class="sb-team" :class="{ win: rightWin }" @click="swapTeams">
          <div class="sb-player">
            <van-image lazy-load round width="40" height="40" :src="rightTeam.a.avatar || defaultAvatar" />
            <span>{{ rightTeam.a.username }}</span>
          </div>
          <div class="sb-player">
            <van-image lazy-load round width="40" height="40" :src="rightTeam.b.avatar || defaultAvatar" />
            <span>{{ rightTeam.b.username }}</span>
          </div>
        </div>
      </div>

      <div class="support-bar" v-if="match" :class="{ 'support-disabled': supportBlocked }">
        <div class="support-track">
          <div class="support-inner">
            <div class="support-fill a" :style="{ flex: supportA }" @click.stop="onSupportClick('a')">
              <div class="support-avatars">
                <img v-for="(av, i) in (swapped ? match.support_b_users : match.support_a_users)" :key="'a'+i" :src="av || defaultAvatar" class="support-av" :style="{ zIndex: (swapped ? match.support_b_users.length : match.support_a_users.length) - i }" />
              </div>
            </div>
            <div class="support-fill b" :style="{ flex: supportB }" @click.stop="onSupportClick('b')">
              <div class="support-avatars">
                <img v-for="(av, i) in (swapped ? match.support_a_users : match.support_b_users)" :key="'b'+i" :src="av || defaultAvatar" class="support-av" :style="{ zIndex: (swapped ? match.support_a_users.length : match.support_b_users.length) - i }" />
              </div>
            </div>
          </div>
        </div>
        <div class="support-labels">
          <span class="support-label" :class="{ active: match.my_support === (swapped ? 'b' : 'a') }" @click="onSupportClick('a')">🔥 {{ swapped ? (match.support_b || 0) : (match.support_a || 0) }} 票</span>
          <span class="support-label" :class="{ active: match.my_support === (swapped ? 'a' : 'b') }" @click="onSupportClick('b')">🔥 {{ swapped ? (match.support_a || 0) : (match.support_b || 0) }} 票</span>
        </div>
        <div class="support-hint" v-if="canSupport && match.status !== 'finished' && !readOnly">点击支持你喜欢的队伍</div>
      </div>

      <!-- 已结束的比赛：默认只读。要改必须先点「修正比赛」并确认，进入修正模式后
           加减只改本地数字，点「确认修正」才真正提交 —— 避免误触直接改掉成绩 -->
      <div v-if="canOperate && isFinishedMatch && !fixMode" class="no-role">
        <p>本场已结束</p>
        <van-button type="warning" block round size="large" style="margin-top:16px" @click="startFix">修正比赛</van-button>
      </div>

      <div v-else-if="canOperate && fixMode" class="controls">
        <div class="swap-hint">修正模式：下面的加减只改本地数字，点「确认修正」才会提交并重算胜负</div>
        <div class="wheel-board">
          <div class="wheel-side">
            <button class="wheel-btn plus" @click="fixA = Math.min(fixA + 1, 99)">+</button>
            <div class="wheel"><span class="wheel-item mid">{{ fixA }}</span></div>
            <button class="wheel-btn minus" :disabled="fixA <= 0" @click="fixA = Math.max(fixA - 1, 0)">-</button>
          </div>
          <div class="wheel-colon">:</div>
          <div class="wheel-side">
            <button class="wheel-btn plus" @click="fixB = Math.min(fixB + 1, 99)">+</button>
            <div class="wheel"><span class="wheel-item mid">{{ fixB }}</span></div>
            <button class="wheel-btn minus" :disabled="fixB <= 0" @click="fixB = Math.max(fixB - 1, 0)">-</button>
          </div>
        </div>
        <van-button type="primary" block round size="large" style="margin-top:20px" :loading="fixSubmitting" @click="confirmFix">确认修正</van-button>
        <van-button plain block round size="large" style="margin-top:10px" @click="fixMode = false">取消</van-button>
      </div>

      <div v-else-if="canOperate" class="controls">
        <div class="wheel-board">
          <div class="wheel-side">
            <button class="wheel-btn plus" :disabled="!canOperate" @click="addScore(swapSide('a'))">+</button>
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
            <button class="wheel-btn minus" :disabled="!canOperate || leftScore <= 0" @click="subScore(swapSide('a'))">-</button>
          </div>

          <div class="wheel-colon">:</div>

          <div class="wheel-side">
            <button class="wheel-btn plus" :disabled="!canOperate" @click="addScore(swapSide('b'))">+</button>
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
            <button class="wheel-btn minus" :disabled="!canOperate || rightScore <= 0" @click="subScore(swapSide('b'))">-</button>
          </div>
        </div>
        <van-button type="danger" block round size="large" style="margin-top:20px" @click="endMatch" :disabled="!canOperate">结束比赛</van-button>
      </div>

      <div v-else-if="locked" class="no-role">
        <p>赛事已结束，本场已锁定</p>
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
import { useResumeRefresh } from '@/composables/useResumeRefresh'
import { serverElapsedSeconds, displayElapsedSeconds, timerDisplay, ONGOING_DISPLAY_LIMIT_SECONDS } from '@/utils/timer'
import { useGoBack } from '@/composables/useGoBack'
import api from '@/api/client'
import { showToast, showConfirmDialog } from 'vant'

const route = useRoute()
const { goBack } = useGoBack()
const matchNum = computed(() => route.query.num ? `第${route.query.num}场` : '记分')
const auth = useAuthStore()
const match = ref<any>(null)
// 比赛不存在（或直链失效、网络异常）时的失败态：
// 原先只有 loading 分支，这些情况会永远转圈且没有返回入口
const loadFailed = ref(false)
// 左右场地交换改为服务端状态（仅裁判可操作），所有人实时同步。
// 这里不再保留本地 ref，避免出现「双数据源」导致各端不一致。
const swapped = computed(() => !!match.value?.is_swapped)
const defaultAvatar = 'https://img.yzcdn.cn/vant/cat.jpeg'

// --- 比赛持续时长（服务端为准，所有人一致） ---
// 时间基准来自服务端的 matches.started_at，配合响应里的 now 换算成「已进行秒数」，
// 因此不依赖任何一台设备的本机时钟：裁判和观众看到的耗时相同。
// 本地只在两次刷新之间每秒递增，保持走字平滑。
const tick = ref(0)
const serverElapsed = ref(0)     // 最近一次拉取时，服务端认可的已进行秒数
const anchorNow = ref(0)         // 最近一次拉取的本地时刻，用于本地外推
let timer: ReturnType<typeof setInterval> | null = null
const durationSecs = computed<number | null>(() => match.value?.duration_seconds ?? null)

const elapsed = computed(() => {
  // 显式读取 tick：Date.now() 本身不是响应式的，靠每秒递增 tick 触发重算
  tick.value
  return displayElapsedSeconds(match.value?.status, durationSecs.value, serverElapsed.value, anchorNow.value, Date.now())
})
const timerText = computed(() => timerDisplay(match.value?.status, elapsed.value, durationSecs.value))
// 超长比赛（多为忘记结束）时用醒目样式提示
const timerOverdue = computed(() => match.value?.status === 'ongoing' && elapsed.value > ONGOING_DISPLAY_LIMIT_SECONDS)

/** 用服务端返回的 started_at 与 now 校准基准（差值与时区无关，两端都是服务端本地时间） */
function syncTimerAnchor() {
  serverElapsed.value = serverElapsedSeconds(match.value?.started_at, match.value?.now)
  anchorNow.value = Date.now()
}

// 走字：仅在「进行中」时每秒递增；结束后停止，避免无意义的重算
watch(
  () => match.value?.status === 'ongoing',
  (ongoing) => {
    if (timer) { clearInterval(timer); timer = null }
    if (ongoing) timer = setInterval(() => { tick.value++ }, 1000)
  },
  { immediate: true },
)
// 每 30s 拉一次校准（服务端有 ETag，内容未变时只回 304，开销很小）
const calibrateTimer = setInterval(() => {
  // 挂 catch：离线时这个定时器会每 30 秒产生一次未处理的 Promise 拒绝
  if (match.value?.status === 'ongoing') fetchMatch().catch(() => {})
}, 30000)
// --- 时长逻辑结束 ---

// 只有在任裁判才有记分/交换场地权限；referee 可能只是历史记录（已卸任）
// 管理员 / 超级管理员：与裁判权限的兜底关系（见下面的 canOperate）
const isPrivileged = computed(() => auth.user?.role === 'admin' || auth.user?.role === 'superadmin')
// 本场在任裁判（严格是谁在任，不含超管）
const isReferee = computed(() => match.value?.active_referee?.id === auth.user?.id)
// 只读锁定：赛事一结束就完全只读 —— 连超级管理员也不能再改（产品规则）
const locked = computed(() => readOnly.value)
// 能否操作本场（记分 / 结束比赛 / 交换场地）—— 注意按钮的 disabled 与操作函数里的
// 守卫都要用它，否则会出现「按钮是灰的/点了没反应」但界面又提示可以修改的矛盾：
//   · 超级管理员：任何时候都行（含赛事结束后、比赛结束后的修正与重算胜负）
//   · 其他人：必须是本场在任裁判，且赛事未结束、比赛未结束
// 与后端 update_score / swap_sides 的判据保持一致。
const canOperate = computed(() => {
  if (!match.value) return false
  if (readOnly.value) return false        // 赛事已结束：完全只读，超管也不行
  if (isPrivileged.value) return true     // 赛事进行中：比赛结束后也能修正结果
  return isReferee.value && match.value.status !== 'finished'
})
const isFinishedMatch = computed(() => match.value?.status === 'finished')
// 修正模式：已结束的比赛默认只读，必须点「修正比赛」→ 确认 → 改本地数字 → 「确认修正」
// 才提交，避免误触一下加号就把成绩改掉
const fixMode = ref(false)
const fixA = ref(0)
const fixB = ref(0)
const fixSubmitting = ref(false)

async function startFix() {
  try {
    await showConfirmDialog({
      title: '修正比赛结果',
      message: '本场已结束。进入修正模式后，加减只改本地数字，点「确认修正」才会真正提交并重算胜负。',
      confirmButtonText: '进入修正',
      cancelButtonText: '取消',
    })
  } catch {
    return // 取消
  }
  // 按记分牌的左右顺序取值（记分牌左右是 swapSide 映射过的，直接取 score_a/b 会改错队）
  fixA.value = leftScore.value
  fixB.value = rightScore.value
  fixMode.value = true
}

async function confirmFix() {
  if (fixSubmitting.value) return // 弹确认框期间连点会叠出多个弹窗，残留那个再确认就是第二次提交
  if (fixA.value === fixB.value) {
    showToast('比分不能相同')
    return
  }
  try {
    await showConfirmDialog({
      title: '确认修正',
      message: `比分将改为 ${fixA.value} : ${fixB.value}，并重算本场胜负。`,
      confirmButtonText: '确认修正',
      cancelButtonText: '取消',
    })
  } catch {
    return // 取消
  }
  fixSubmitting.value = true
  try {
    // force_end：改完比分顺便重算胜负（后端对超管允许对已结束的比赛再结束一次）
    await api.put(`/tournaments/${route.params.id}/matches/${route.params.matchId}/score`, {
      // 提交时映射回真实的 a/b（修正面板里 fixA/fixB 存的是左右顺序）
      score_a: swapped.value ? fixB.value : fixA.value,
      score_b: swapped.value ? fixA.value : fixB.value,
      force_end: true,
    })
    showToast('已修正')
    fixMode.value = false
    await fetchMatch().catch(() => {})
  } catch {
    // 失败提示由拦截器统一弹
  } finally {
    fixSubmitting.value = false
  }
}
// 赛事已提前结束时，该场即使还没打完也整体只读（服务端也会拒绝所有写操作）
// 只读锁定：后端要求赛事**恰好进行中**才允许写（load_writable_tournament），
// 所以这里用 !== 'ongoing' 保持等价，避免出现"界面给操作但后端 400"
const readOnly = computed(() => match.value?.tournament_status !== 'ongoing')

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
  // 只有在任裁判不能投票：已卸任者（referee 有值但 active_referee 为空）可以正常应援，
  // 与服务端 POST /support 的校验保持一致
  if (match.value.active_referee?.id === auth.user.id) return false
  return true
})
// 应援是否被禁止（赛事已结束 / 比赛已结束 / 自己不可投票）。
// 用于把应援条置灰并在点击时给出原因，而不是静默无反应。
const supportBlocked = computed(() =>
  !canSupport.value || readOnly.value || match.value?.status === 'finished'
)
function onSupportClick(side: string) {
  if (!supportBlocked.value) {
    doSupport(side)
    return
  }
  // 给出明确反馈：原先这些情况下点击完全没反应，用户会以为手势没生效
  if (readOnly.value) showToast('赛事已结束，不能再应援')
  else if (match.value?.status === 'finished') showToast('比赛已结束，不能再应援')
  else if (!auth.user) showToast('请先登录')
  else showToast('参赛选手与在任裁判不能应援')
}
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

async function swapTeams() {
  // 只有本场裁判可以交换场地；其他人在自己屏幕上点无效（服务端也会拒绝）
  if (!canOperate.value) return
  try {
    const res = await api.post(
      `/tournaments/${route.params.id}/matches/${route.params.matchId}/swap-sides`,
      {}, { skipLoading: true } as any,
    )
    // 用返回值立即落本地状态，不要只等广播：WS 恰好在重连窗口时广播会丢，
    // 那时界面毫无反馈，裁判会以为按钮坏了（服务端已翻转，下次刷新还会跳变）。
    // 广播到达时再赋同一个值，是幂等的。
    if (res.data?.is_swapped != null) match.value.is_swapped = res.data.is_swapped
  } catch {
    // 失败（例如刚被卸任、赛事已结束）：拦截器已提示，界面保持原状
  }
}
function swapSide(side: string) { return (!swapped.value) ? side : (side === 'a' ? 'b' : 'a') }

async function fetchMatch() {
  try {
    const res = await api.get(`/tournaments/${route.params.id}/matches/${route.params.matchId}`, { skipLoading: true } as any)
    // 本地有尚未提交的乐观比分时予以保留：否则响应里还是旧比分，
    // 会把裁判刚点的那一分覆盖回去（随后 flush 才写回，表现为闪一下）
    if (pendingScore && match.value) {
      res.data.score_a = match.value.score_a
      res.data.score_b = match.value.score_b
    }
    match.value = res.data
    syncTimerAnchor()
    loadFailed.value = false
  } catch (e) {
    // 比赛不存在/已删除或网络异常：原先页面会永远停在 loading 转圈，
    // 没有任何返回或重试入口，只能靠浏览器后退
    if (!match.value) loadFailed.value = true
    throw e
  }
}

async function doSupport(side: string) {
  // 赛事结束（只读）后不再应援：服务端也会拒绝，这里提前拦住避免无声失败
  if (!canSupport.value || readOnly.value || match.value?.status === 'finished') return
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
// 乐观改动前的「已确认」比分，用于写库失败时回滚。
// 必须在改动之前记录：flushScoreNow 是防抖后 300ms 才执行的，
// 那时 match.value 里已经是加过的值，在那里取 prev 等于没回滚。
let confirmedScore: { score_a: number; score_b: number } | null = null
const wheelSide = ref<string | null>(null)
let wheelGesture: { id: number; y: number; acc: number; side: string } | null = null

async function flushScoreNow() {
  if (!pendingScore || !match.value) return
  const data = pendingScore
  // 回滚目标：本批乐观改动之前的已确认比分
  const prev = confirmedScore ?? { score_a: match.value.score_a ?? 0, score_b: match.value.score_b ?? 0 }
  pendingScore = null
  confirmedScore = null
  try {
    await api.put(
      `/tournaments/${route.params.id}/matches/${route.params.matchId}/score`,
      data, { skipLoading: true } as any,
    )
  } catch {
    // 写库失败（例如刚被卸任 → 403、比赛已结束 → 400、网络异常）：
    // 乐观加的那一分服务端并不存在，必须回滚，否则界面会长期显示一个幻影比分。
    if (match.value) {
      match.value.score_a = prev.score_a
      match.value.score_b = prev.score_b
    }
    await fetchMatch().catch(() => {})
    // 期间用户又点了，按回滚后的基准重新提交
  }
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
  // 只在「本次乐观改动链的第一次」记录回滚目标，多次连点不能被中间值覆盖
  if (!confirmedScore) confirmedScore = { score_a: match.value.score_a ?? 0, score_b: match.value.score_b ?? 0 }
  if (side === 'a') match.value.score_a = (match.value.score_a ?? 0) + 1
  else match.value.score_b = (match.value.score_b ?? 0) + 1
  pendingScore = { score_a: match.value.score_a ?? 0, score_b: match.value.score_b ?? 0 }
  scheduleFlush()
}

async function subScore(side: string) {
  if (!match.value) return
  if (side === 'a' && (match.value.score_a ?? 0) > 0) {
    if (!confirmedScore) confirmedScore = { score_a: match.value.score_a ?? 0, score_b: match.value.score_b ?? 0 }
    match.value.score_a -= 1
  } else if (side === 'b' && (match.value.score_b ?? 0) > 0) {
    if (!confirmedScore) confirmedScore = { score_a: match.value.score_a ?? 0, score_b: match.value.score_b ?? 0 }
    match.value.score_b -= 1
  } else {
    return
  }
  pendingScore = { score_a: match.value.score_a ?? 0, score_b: match.value.score_b ?? 0 }
  scheduleFlush()
}

function wheelDown(e: PointerEvent, side: string) {
  if (!canOperate.value) return
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
  showToast('比赛结束')
  goBack()
}

async function claimReferee() {
  // 本场已有在任裁判时先确认：认领会**顶替**对方（裁判转让 / 借手机代认领的场景）
  const cur = match.value?.active_referee
  if (cur && cur.id !== auth.user?.id) {
    try {
      await showConfirmDialog({
        title: '顶替本场裁判',
        message: `本场裁判是「${cur.username}」。确认后改由你执裁，对方将失去记分权限（会收到站内消息）。`,
        confirmButtonText: '顶替',
        cancelButtonText: '取消',
      })
    } catch {
      return // 取消
    }
  }
  try {
    await api.post(`/tournaments/${route.params.id}/matches/${route.params.matchId}/claim-referee`)
    showToast('认领成功')
  } catch {
    // 赛事已结束等异常路径：拦截器已提示。
    // 这里不往外抛，避免未处理的 Promise 拒绝
  } finally {
    // 无论成功失败都刷新：失败时界面需要从「可认领」更新为真实状态
    await fetchMatch().catch(() => {})
  }
}

const tid = Number(route.params.id)
const { lastMessage } = useWebSocket(tid)
watch(lastMessage, (msg) => {
  if (!msg) return
  // match 还没加载出来（首屏请求未返回或失败）时不要往下走：
  // 否则下面几处 match.value.x 会抛 TypeError，这次实时更新就丢了
  if (!match.value) return
  if (msg.type === 'match_updated' && msg.match_id === Number(route.params.matchId)) {
    if (!pendingScore) {
      if (msg.score_a != null) match.value.score_a = msg.score_a
      if (msg.score_b != null) match.value.score_b = msg.score_b
    }
    if (msg.status) {
      const wasPending = match.value.status !== 'ongoing'
      match.value.status = msg.status
      // 计时基准以服务端给的 started_at 为准（每次广播都会带上真实开赛时间）。
      // 绝不能改用 msg.now：那是广播时刻，会让计时器每次记分归零重走。
      if (msg.started_at) {
        match.value.started_at = msg.started_at
        match.value.now = msg.now ?? match.value.now
        syncTimerAnchor()
      } else if (msg.status === 'ongoing' && msg.now && wasPending) {
        // 兼容旧服务的兜底：只在「由未开始变为进行中」这一刻采用 now 作为起点
        match.value.started_at = msg.now
        match.value.now = msg.now
        syncTimerAnchor()
      }
      // 比赛结束的广播会带上最终耗时，直接采用，省一次请求
      if (msg.duration_seconds != null) match.value.duration_seconds = msg.duration_seconds
      // 胜方也要采用，否则观众看不到胜方高亮/🏆（结束之后不会再有任何补拉）
      if (msg.winner_pairing_id != null) match.value.winner_pairing_id = msg.winner_pairing_id
    }
    // 裁判交换场地后，所有正在看这场的人一起切换
    if (msg.is_swapped != null) match.value.is_swapped = msg.is_swapped
  }
  if (msg.type === 'support_updated' && msg.match_id === Number(route.params.matchId)) {
    match.value.support_a = msg.support_a
    match.value.support_b = msg.support_b
    fetchMatch().catch(() => {})
  }
  // 认领 / 卸任 / 被顶替：裁判身份变了，权限与页面状态要立刻跟上。
  // 尤其「自己认领的场次被别人顶替」时，不刷新的话按钮还停在记分态，
  // 下一次记分才报 403 —— 那时用户只会觉得「点了没反应」。
  if (
    (msg.type === 'referee_claimed' || msg.type === 'referee_released')
    && msg.match_id === Number(route.params.matchId)
  ) {
    fetchMatch().catch(() => {})
  }
})

// 锁屏/后台返回时补一次刷新：冻结期间定时器与 WebSocket 都可能失效，
// 回来时主动拉取，保证比分、状态与耗时都是最新的
useResumeRefresh(async () => {
  await fetchMatch()
})

onMounted(async () => {
  try {
    await Promise.all([auth.fetchMe(), fetchMatch()])
  } catch {
    // fetchMatch 已把 loadFailed 置位，界面显示失败态；拦截器也已提示
  }
})

onUnmounted(() => {
  if (timer) clearInterval(timer)
  clearInterval(calibrateTimer)
})
</script>

<style scoped>
.score-root { background: #f0f2f5; padding-bottom: 40px; }
.readonly-banner {
  display: flex; align-items: center; justify-content: center; gap: 6px;
  margin: 10px 12px 0; padding: 10px 12px;
  font-size: 13px; color: #8a6d3b;
  background: #fdf6e3; border: 1px solid #f5e2b8; border-radius: 10px;
}
.loading { display: flex; justify-content: center; margin-top: 120px; }
.load-failed {
  display: flex; flex-direction: column; align-items: center; gap: 6px;
  padding: 100px 24px 0; color: #969799; font-size: 14px; text-align: center;
}
.load-failed p { margin: 0; }
.load-failed-sub { font-size: 12px; color: #c8c9cc; margin-bottom: 8px !important; }
/* 导航栏中间：场次（耗时）。用等宽数字，避免计时跳秒时标题左右抖动 */
.nav-title { font-variant-numeric: tabular-nums; }
/* 比赛超过最长时长（多半忘记结束）时用醒目色提示 */
.nav-title.overdue { color: #ee0a24; }
/* 导航栏右侧的积分榜入口 */
.nav-rank { font-size: 14px; color: #1989fa; }

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
/* 不可应援时（赛事/比赛已结束，或自己不能投票）整体置灰：
   与点击时弹出的原因保持一致，避免用户以为手势没生效 */
.support-bar.support-disabled { opacity: .55; }
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
