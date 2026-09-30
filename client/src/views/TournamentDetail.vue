<template>
  <div class="detail-page vh-page">
    <van-nav-bar title="赛事详情" left-text="返回" left-arrow @click-left="goBack">
      <template #right>
        <!-- 报名中才有分享海报的意义（二维码指向报名页）；图标与积分榜的分享入口一致 -->
        <span class="nav-right">
          <van-icon v-if="tournament && tournament.status === 'open'" name="share-o" size="19" @click="openPoster" />
          <van-icon v-if="canDelete" name="delete-o" size="20" @click="doDelete" />
        </span>
      </template>
    </van-nav-bar>

    <div class="detail-scroll">
    <van-pull-refresh v-model="refreshing" @refresh="onRefresh" class="pull-fill">
      <div class="pull-inner">
    <van-loading v-if="!tournament && !loadFailed" class="loading" />
    <div v-else-if="!tournament && loadFailed" class="load-failed">
      <van-icon name="warning-o" size="24" />
      <p>赛事加载失败</p>
      <p class="load-failed-sub">赛事可能已被删除，或网络异常</p>
      <van-button size="small" round plain type="primary" @click="goBack">返回</van-button>
    </div>
    <template v-else>
      <div class="info-card">
        <div class="info-head">
          <h2>{{ tournament.title }}</h2>
          <van-tag :type="statusType" size="medium" round>{{ tournament.status === 'open' ? (openLocked ? '即将开放' : '报名中') : tournament.status === 'ongoing' ? '进行中' : '已结束' }}</van-tag>
        </div>
        <p class="info-desc" v-if="tournament.description">{{ tournament.description }}</p>
        <div class="info-grid">
          <div class="ig-item"><van-icon name="location-o" /><span>{{ tournament.location || '待定' }}</span></div>
          <div class="ig-item" v-if="tournament.courts && tournament.courts.length > 0"><van-icon name="guide-o" /><span>场地号{{ tournament.courts[0].name }}</span></div>
          <div class="ig-item"><van-icon name="clock-o" /><span>{{ fmtDateTime(tournament.start_date, tournament.end_date) }}</span></div>
          <div class="ig-item"><van-icon name="friends-o" /><span>{{ tournament.registered_count }}/{{ tournament.max_participants }} 人</span></div>
          <div class="ig-item"><van-icon name="orders-o" /><span>{{ totalMatchesText }}</span></div>
          <div class="ig-item"><van-icon name="medal-o" /><span>{{ tournament.points_to_win || 11 }} 分制</span></div>
        </div>
      </div>

      <div class="action-block" v-if="tournament.status === 'open'">
        <div v-if="openLocked" class="reg-countdown">距报名开放还有 {{ countdownText }}</div>
        <van-button type="primary" block round :loading="submitting" :disabled="submitting || tournament.is_registered || openLocked || regFull" @click="doRegister">
          {{ tournament.is_registered ? '已报名' : (regFull ? '人数已满' : '立即报名') }}
        </van-button>
        <van-button v-if="tournament.is_registered && tournament.status === 'open'" plain block round style="margin-top:8px" :loading="submitting" :disabled="submitting" @click="doCancelRegister">
          取消报名
        </van-button>
      </div>

      <div class="action-block" v-if="tournament.is_registered && tournament.status === 'ongoing'">
        <van-button type="warning" plain block round :loading="submitting" :disabled="submitting" @click="doWithdraw">
          退出比赛
        </van-button>
      </div>

      <div class="player-section">
        <div class="player-head">
          <span class="ph-title">已报名 ({{ tournament.registered_count }})</span>
          <span class="ph-actions">
            <span class="ph-action" @click="copyTournamentLink">
              <van-icon name="link-o" /> 复制链接
            </span>
            <span v-if="tournament.cancelled_count > 0" class="ph-action" @click="openCancellations">
              <van-icon name="records" /> 取消/退赛 {{ tournament.cancelled_count }}
            </span>
          </span>
        </div>
        <div class="player-grid" v-if="registrations.length > 0">
          <div v-for="r in registrations" :key="r.id" class="player-chip" @click.stop="viewPlayer(r)">
            <div class="avatar-badge-sm">
              <van-image lazy-load round width="40" height="40" :src="r.avatar || defaultAvatar" />
              <span v-if="r.user_id === tournament.creator_id" class="host-badge">房主</span>
            </div>
            <span class="player-name">{{ r.username }}</span>
            <span class="player-time">{{ formatTime(r.created_at) }}</span>
          </div>
        </div>
        <p v-else-if="regsFailed" class="empty-hint">报名列表加载失败，请下拉重试</p>
        <p v-else class="empty-hint">暂无报名</p>
      </div>

      <div class="creator-block" v-if="canManage && tournament.status === 'ongoing'">
        <van-button type="danger" block round :loading="submitting" :disabled="submitting" @click="doEndTournament">提前结束赛事</van-button>
      </div>

      <div class="nav-block" v-if="tournament.status !== 'open'">
        <van-grid :column-num="2" clickable :border="false">
          <van-grid-item icon="clock-o" text="对阵表" @click="$router.push(`/tournament/${tournament.id}/schedule`)" />
          <van-grid-item icon="chart-trending-o" text="积分榜" @click="$router.push(`/tournament/${tournament.id}/rankings`)" />
        </van-grid>
      </div>

      <div class="creator-block" v-if="canManage && tournament.status === 'open'">
        <van-button type="danger" block round :loading="submitting" :disabled="submitting || tournament.registered_count < 4" @click="doStart">
          开始比赛<template v-if="tournament.registered_count < 4">（还需 {{ 4 - tournament.registered_count }} 人）</template>
        </van-button>
      </div>

    <van-popup v-model:show="showPlayerStats" round position="bottom" class="stats-popup vh-sheet vh-65" lock-scroll>
       <div class="popup-content vh-sheet-body" @touchmove.stop>
          <div class="popup-player-head">
            <van-image lazy-load round width="56" height="56" :src="playerDetail.avatar || defaultAvatar" />
            <h3>{{ playerDetail.username }}</h3>
          </div>
          <van-cell-group inset v-if="playerStats.total_matches > 0">
            <van-cell title="总场次" :value="String(playerStats.total_matches)" />
            <van-cell title="胜场" :value="String(playerStats.total_wins)" />
            <van-cell title="负场" :value="String(playerStats.total_matches - playerStats.total_wins)" />
            <van-cell title="胜率" :value="`${playerStats.win_rate}%`">
              <template #label>双打胜率，按参与场次统计</template>
            </van-cell>
            <van-cell title="参赛次数" :value="String(playerStats.tournaments_played)" />
          </van-cell-group>
          <van-empty v-else :description="statsFailed ? '战绩加载失败，请重试' : '暂无比赛记录'" />
        </div>
      </van-popup>

      <!-- 取消报名 / 赛中退赛记录：两者都是标记失效、不删记录，所以谁退出过查得到 -->
      <van-popup v-model:show="showCancellations" round position="bottom" class="vh-sheet vh-50" lock-scroll>
        <div class="picker-toolbar">
          <span @click="showCancellations = false">关闭</span>
          <span class="picker-title">取消报名 / 退赛记录（{{ cancellations.length }}）</span>
        </div>
        <div class="vh-sheet-body cancel-body">
          <div v-for="c in cancellations" :key="c.user_id" class="cancel-row">
            <van-image lazy-load round width="32" height="32" :src="c.avatar || defaultAvatar" />
            <span class="cancel-name">{{ c.username }}</span>
            <!-- 区分两种离场方式：报名阶段取消 vs 开赛后退出（后者一般已经打过比赛） -->
            <span class="cancel-kind" :class="{ withdraw: c.kind === 'withdraw' }">
              {{ c.kind === 'withdraw' ? '退赛' : '取消' }}
            </span>
            <span class="cancel-time">{{ c.cancelled_at ? formatTime(c.cancelled_at) : '时间未知' }}</span>
          </div>
          <van-empty v-if="cancellationsFailed" description="加载失败，请重试" />
          <van-empty v-else-if="cancellations.length === 0" description="暂无取消 / 退赛记录" />
        </div>
      </van-popup>

    </template>
      </div>
    </van-pull-refresh>
    </div>
  </div>
      <!-- 场次重选弹窗 -->
      <van-popup v-model:show="showMatchPicker" position="bottom" round class="vh-sheet vh-45">
        <div class="picker-toolbar">
          <span @click="showMatchPicker = false">取消</span>
          <span class="picker-title">当前报名 {{ tournament.registered_count }} 人，请选择总场次</span>
        </div>
        <!-- 弹层外壳固定，只有这一层滚动（.vh-sheet-body） -->
        <div class="vh-sheet-body">
        <van-cell-group inset style="margin-top:10px">
          <van-cell
            v-for="opt in matchOptions" :key="opt.total"
            :title="`${opt.total} 场`" :label="`每人 ${opt.per_person} 场`"
            @click="selectMatchStart(opt.total)" :class="{ active: matchTotal === opt.total }"
          />
        </van-cell-group>
        </div>
      </van-popup>

    <van-popup v-model:show="showTransferPicker" round position="bottom" class="vh-sheet vh-50" lock-scroll>
      <div class="popup-content vh-sheet-body" @touchmove.stop>
        <h3>选择新房主</h3>
        <div class="popup-grid">
          <div
            v-for="r in otherActivePlayers"
            :key="r.id"
            class="transfer-player"
            :class="{ selected: selectedNewCreator === r.user_id }"
            @click="selectedNewCreator = r.user_id"
          >
            <van-image lazy-load round width="40" height="40" :src="r.avatar || defaultAvatar" />
            <span>{{ r.username }}</span>
          </div>
        </div>
        <van-empty v-if="!canTransfer" image-size="60" description="暂无其他报名者可接手" />
        <div style="padding: 12px 16px;">
          <van-button type="primary" block round :disabled="!selectedNewCreator" @click="doTransferAndWithdraw">确认转让并退出</van-button>
        </div>
      </div>
    </van-popup>

    <!-- 报名海报预览（遮罩组件与积分榜分享图共用） -->
    <ShareImageOverlay
      v-if="posterUrl"
      :url="posterUrl"
      :file="posterFile"
      :file-name="POSTER_FILE"
      :share-title="tournament?.title || '羽毛球局'"
      :tip="posterTip"
      @close="closePoster"
    />

</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import api from '@/api/client'
import { useWebSocket } from '@/composables/useWebSocket'
import { useResumeRefresh } from '@/composables/useResumeRefresh'
import { useGoBack } from '@/composables/useGoBack'
import { copyText } from '@/utils/clipboard'
import { renderSignupPoster, POSTER_FILE } from '@/utils/poster'
import ShareImageOverlay from '@/components/ShareImageOverlay.vue'
import { showToast, showConfirmDialog, showLoadingToast, showFailToast } from 'vant'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const { goBack } = useGoBack()
const tid = Number(route.params.id)
const { lastMessage } = useWebSocket(tid)

const refreshing = ref(false)
const tournament = ref<any>(null)
// 写操作进行中：防止连点导致重复提交与矛盾提示
// （例如连点「立即报名」会先后弹出「报名成功」和「已报名」两条 toast）
const submitting = ref(false)
// 赛事不存在/加载失败：原先只有 loading 分支，404 会永远转圈
const loadFailed = ref(false)
const registrations = ref<any[]>([])
// 报名列表拉取失败（与「暂无报名」区分开）
const regsFailed = ref(false)
const showPlayerStats = ref(false)
const showMatchPicker = ref(false)
const showTransferPicker = ref(false)
// 除自己外没有其他报名者：房主此时无法「转让后退出」（弹窗里没有候选），
// 需要走「直接退出 + 赛事自动结束」这条路（后端在剩余人数不足 4 人时会自动结束）
const otherActivePlayers = computed(() =>
  (registrations.value || []).filter(r => r.user_id !== auth.user?.id && r.is_active !== false)
)
const canTransfer = computed(() => otherActivePlayers.value.length > 0)
const selectedNewCreator = ref(0)
const matchOptions = ref<{ total: number; per_person: number }[]>([])
const matchTotal = ref(0)
const playerDetail = ref<any>({})
const playerStats = ref<any>({})
const statsFailed = ref(false)
const defaultAvatar = 'https://img.yzcdn.cn/vant/cat.jpeg'

// --- 定时开放报名倒计时（以服务端时间校准客户端时钟偏差）---
let clockOffset = 0
let countdownTimer: ReturnType<typeof setInterval> | null = null
const nowTick = ref(Date.now())
const remainingMs = computed(() => {
  const openAt = tournament.value?.registration_open_at
  if (!openAt) return 0
  return Math.max(0, new Date(openAt).getTime() - clockOffset - nowTick.value)
})
const openLocked = computed(() => remainingMs.value > 0)
// 报名人数已满时禁用「立即报名」：仅靠后端返回 400 会让用户点了才知道
const regFull = computed(() => {
  const t = tournament.value
  if (!t) return false
  return (t.registered_count ?? 0) >= (t.max_participants ?? 0)
})
const countdownText = computed(() => {
  if (!openLocked.value) return ''
  const s = Math.floor(remainingMs.value / 1000)
  const d = Math.floor(s / 86400)
  const h = Math.floor((s % 86400) / 3600)
  const m = Math.floor((s % 3600) / 60)
  const sec = s % 60
  const pad = (n: number) => String(n).padStart(2, '0')
  return d > 0 ? `${d}天 ${pad(h)}:${pad(m)}:${pad(sec)}` : `${pad(h)}:${pad(m)}:${pad(sec)}`
})
function syncServerClock(serverNow?: string) {
  if (serverNow) clockOffset = new Date(serverNow).getTime() - Date.now()
}
function startCountdown() {
  stopCountdown()
  countdownTimer = setInterval(() => { nowTick.value = Date.now() }, 1000)
}
function stopCountdown() {
  if (countdownTimer) { clearInterval(countdownTimer); countdownTimer = null }
}
function onVisibilityChange() {
  // 从后台切回时立即刷新，避免浏览器节流定时器导致按钮延迟放开
  if (!document.hidden) nowTick.value = Date.now()
}

const isCreator = computed(() => auth.user?.id === tournament.value?.creator_id)
// 开赛 / 提前结束赛事：创建者、管理员或超级管理员（兜底，房主临时不在也能推进流程）
const canManage = computed(() => isCreator.value || auth.user?.role === 'admin' || auth.user?.role === 'superadmin')
const canDelete = computed(() => {
  if (!auth.user || !tournament.value) return false
  // 与后端一致：只有报名中的赛事能删（已开赛/已结束的不能删，管理员/超管也一样）
  if (tournament.value.status !== 'open') return false
  if (auth.user.role === 'admin' || auth.user.role === 'superadmin') return true
  return auth.user.id === tournament.value.creator_id
})
const statusType = computed(() => tournament.value?.status === 'open' ? 'primary' : tournament.value?.status === 'ongoing' ? 'success' : 'default')
/** 总场次：开赛前 total_matches 为空（由后端在开赛时按人数自动算），此时如实说明 */
const totalMatchesText = computed(() => {
  const n = tournament.value?.total_matches
  return n ? `共 ${n} 场` : '开赛时自动计算'
})

const WEEKDAYS2 = ['周日', '周一', '周二', '周三', '周四', '周五', '周六']
function fmtDateTime(start: string, end: string) {
  if (!start || !end) return ''
  const d = new Date(start)
  const y = d.getFullYear()
  const m = d.getMonth() + 1
  const day = d.getDate()
  const w = WEEKDAYS2[d.getDay()]
  return `${y}年${m}月${day}日(${w}) ${start.slice(11, 16)}~${end.slice(11, 16)}`
}
function formatTime(d: string) { if (!d) return ''; return d.replace('T', ' ').slice(5, 16) }

async function viewPlayer(r: any) {
  playerDetail.value = r
  showPlayerStats.value = true
  statsFailed.value = false
  playerStats.value = {}
  try {
    const res = await api.get(`/auth/stats/${r.user_id}`)
    playerStats.value = res.data
  } catch {
    // 不能把「拉取失败」显示成「暂无比赛记录」——那是两回事
    statsFailed.value = true
  }
}

async function fetchDetail(skipLoading = false) {
  try {
    const res = await api.get(`/tournaments/${route.params.id}`, { skipLoading } as any)
    tournament.value = res.data
    syncServerClock(res.data.server_now)
    loadFailed.value = false
  } catch (e: any) {
    // 赛事被删除或直链失效时，原先页面会永远停在 loading 转圈；
    // 这里给出明确的失败态（含返回入口）
    if (!tournament.value) loadFailed.value = true
    throw e
  }
}
async function fetchRegistrations(skipLoading = false) {
  try {
    const res = await api.get(`/tournaments/${route.params.id}/registrations`, { skipLoading } as any)
    registrations.value = res.data
    regsFailed.value = false
  } catch (e) {
    // 不能把「拉取失败」显示成「暂无报名」：那是两回事（与战绩处同一标准）
    if (registrations.value.length === 0) regsFailed.value = true
    throw e
  }
}

/* ---------------- 报名链接分享 / 取消报名记录 ---------------- */
const showCancellations = ref(false)
const cancellations = ref<any[]>([])
const cancellationsFailed = ref(false)

/** 复制当前页面链接（赛事详情就是报名页），发给球友来报名 */
async function copyTournamentLink() {
  const ok = await copyText(location.href)
  showToast(ok ? '链接已复制，发给要报名的球友' : '复制失败，请长按地址栏复制')
}

/* ---------------- 报名海报（发到微信群） ----------------
 * 群里发一张写清「时间 / 地点 / 还剩几个名额」的图，比甩一个"点开先跳登录"的链接
 * 有用得多——报名页要登录，而群里的人多半还没注册。
 * 二维码由后端出图，里面带了当前用户的邀请码：新球友扫码后会被路由守卫带到登录页
 * 并自动预填邀请码（见 router/index.ts），注册完再回到本页。
 */
const posterUrl = ref('')
const posterFile = ref<File | null>(null)
const isWeChat = /MicroMessenger/i.test(navigator.userAgent)

/** 发起人取房主昵称；报名列表还没加载出来时拿不到，海报就整行不画 */
const hostName = computed(() => {
  const cid = tournament.value?.creator_id
  if (!cid) return null
  return registrations.value.find(r => r.user_id === cid)?.username || null
})
const posterStatusText = computed(() => {
  if (!tournament.value) return ''
  if (openLocked.value) return '报名即将开放'
  return regFull.value ? '报名已满' : '报名中'
})
const posterQuotaText = computed(() => {
  const t = tournament.value
  if (!t) return ''
  const left = Math.max(0, (t.max_participants ?? 0) - (t.registered_count ?? 0))
  const base = `${t.registered_count}/${t.max_participants} 人`
  return left > 0 ? `${base}（还剩 ${left} 个）` : base
})
const posterPlaceText = computed(() => {
  const t = tournament.value
  if (!t) return ''
  const court = t.courts?.[0]?.name
  return [t.location || '地点待定', court ? `场地号${court}` : ''].filter(Boolean).join(' ')
})
// 邀请码只有管理员能生成（见 auth.generate_invite），普通成员的海报里就没有邀请码：
// 这种情况下必须说清楚，否则新球友扫码后卡在注册表单前，发海报的人只会以为功能坏了
const hasInviteCode = computed(() => !!auth.user?.invite_code)
const posterTip = computed(() => {
  if (!hasInviteCode.value) {
    return '长按图片保存发给球友；本场报名页需要登录，你还没有邀请码（仅管理员可生成），新球友请先向管理员索取'
  }
  // 微信内没有一键分享图片（iOS 微信把文件交给自己的分享扩展必然失败，见
  // ShareImageOverlay），提示语必须说清"长按保存再回群里发"
  return isWeChat ? '长按图片保存，回到微信群发给球友；球友扫码即可报名' : undefined
})

async function openPoster() {
  const t = tournament.value
  if (!t) return
  const toast = showLoadingToast({ message: '正在生成海报…', forbidClick: true, duration: 0 })
  // 二维码是二进制响应：用 blob 取回来再转成 blob: URL 交给 canvas。
  // 跨域图片会让 canvas 变 tainted、toBlob 直接抛 SecurityError，blob: 不会。
  let qrObjectUrl = ''
  try {
    const res = await api.get(`/tournaments/${t.id}/poster-qr`, {
      responseType: 'blob', skipLoading: true,
    } as any)
    qrObjectUrl = URL.createObjectURL(res.data as Blob)
    const poster = await renderSignupPoster({
      title: t.title,
      statusText: posterStatusText.value,
      timeText: fmtDateTime(t.start_date, t.end_date),
      placeText: posterPlaceText.value,
      quotaText: posterQuotaText.value,
      hostName: hostName.value,
      qrSrc: qrObjectUrl,
    })
    closePoster()   // 释放上一张
    posterUrl.value = poster.url
    posterFile.value = new File([poster.blob], POSTER_FILE, { type: 'image/png' })
  } catch {
    showFailToast('生成海报失败，请重试')
  } finally {
    toast.close()
    // 二维码已经画进 canvas（像素已落定），这个对象 URL 可以立刻释放
    if (qrObjectUrl) URL.revokeObjectURL(qrObjectUrl)
  }
}

function closePoster() {
  if (posterUrl.value) URL.revokeObjectURL(posterUrl.value)
  posterUrl.value = ''
  posterFile.value = null
}

async function fetchCancellations() {
  try {
    const res = await api.get(`/tournaments/${route.params.id}/cancellations`, { skipLoading: true } as any)
    cancellations.value = res.data || []
    cancellationsFailed.value = false
  } catch (e) {
    cancellationsFailed.value = true
    throw e
  }
}

/** 懒加载：只有点了才请求，平时不额外打接口（入口也只在有取消记录时才出现） */
async function openCancellations() {
  showCancellations.value = true
  try {
    await fetchCancellations()
  } catch {
    // 失败态由 cancellationsFailed 呈现，这里不再弹 toast（避免和空态重复提示）
  }
}
/** 写操作统一包一层：进行中忽略重复点击，结束后必定复位 */
async function withSubmitting(fn: () => Promise<void>) {
  if (submitting.value) return
  submitting.value = true
  try {
    await fn()
  } catch {
    // 错误提示交给 axios 拦截器
  } finally {
    submitting.value = false
  }
}

// 刚刚主动刷新过的时间戳：写操作成功后会自己拉一次，紧接着服务端广播又会触发
// 「静默刷新」，白跑两个请求。用这个时间窗把紧随其后的广播刷新合并掉。
let lastRefreshAt = 0
const BROADCAST_REFRESH_SKIP_MS = 1200
async function refreshBoth(skipLoading = false) {
  lastRefreshAt = Date.now()
  await Promise.all([fetchDetail(skipLoading), fetchRegistrations(skipLoading)])
}
function refreshBothIfNotJustRefreshed() {
  if (Date.now() - lastRefreshAt < BROADCAST_REFRESH_SKIP_MS) return
  Promise.all([fetchDetail(true), fetchRegistrations(true)]).catch(() => {})
}

async function doRegister() {
  await withSubmitting(async () => {
    await api.post(`/tournaments/${route.params.id}/register`)
    showToast('报名成功')
    await refreshBoth()
  })
}
async function doCancelRegister() {
  await withSubmitting(async () => {
    await api.post(`/tournaments/${route.params.id}/cancel-register`)
    showToast('已取消报名')
    await refreshBoth()
  })
}

async function doWithdraw() {
  await withSubmitting(async () => {
    const iAmCreator = auth.user?.id === tournament.value?.creator_id
    if (iAmCreator && canTransfer.value) {
      // 有可转让对象：先选新房主再退出
      showTransferPicker.value = true
      return
    }
    if (iAmCreator) {
      // 自己是唯一报名者：没有新房主可选，只能直接退出。
      // 退出后剩余人数不足 4 人，服务端会自动结束赛事。
      try {
        await showConfirmDialog({
          title: '确认退出',
          message: '你是唯一报名者，退出后赛事将自动结束，确定退出？',
        })
      } catch { return }
    } else {
      try { await showConfirmDialog({ title: '确认退出', message: '退出比赛后赛程将重新排列，确定退出？' }) } catch { return }
    }
    await api.post(`/tournaments/${route.params.id}/withdraw/${auth.user!.id}`)
    showToast('已退出比赛')
    await refreshBoth()
  })
}

async function doTransferAndWithdraw() {
  if (!selectedNewCreator.value) return
  await withSubmitting(async () => {
    try {
      const target = registrations.value.find(r => r.user_id === selectedNewCreator.value)
      await showConfirmDialog({
        title: '转让房主并退出',
        message: `退出后房主将转让给 ${target?.username || '所选用户'}，确定退出？`,
      })
    } catch { return }
    await api.post(`/tournaments/${route.params.id}/withdraw/${auth.user!.id}`, { new_creator_id: selectedNewCreator.value })
    showToast('已退出比赛')
    showTransferPicker.value = false
    await refreshBoth()
  })
}
async function fetchMatchOptionsForStart() {
  const n = tournament.value?.registered_count || 0
  if (n < 4) return
  try {
    const res = await api.get(`/tournaments/match-options/${n}`)
    matchOptions.value = res.data.options || []
  } catch {}
}

function selectMatchStart(total: number) {
  matchTotal.value = total
  showMatchPicker.value = false
  doStartWithTotal()
}

async function doStartWithTotal() {
  await withSubmitting(async () => {
    const res = await api.post(`/tournaments/${route.params.id}/start`, { total_matches: Number(matchTotal.value) })
    const actual = res.data?.total_matches ?? res.data?.matches
    // 服务端在个别组合下排不出请求的场次（贪婪排程的边界），会退到可行的最小场次，
    // 此时要告知用户实际场次，避免与自己的选择不符却毫无提示
    if (actual != null && Number(actual) !== Number(matchTotal.value)) {
      showToast(`所选场次无法排出，已按 ${actual} 场开赛`)
    } else {
      showToast('比赛已开始')
    }
    await fetchDetail()
  })
}

async function doStart() {
  const count = tournament.value?.registered_count || 0
  const max = tournament.value?.max_participants || 0
  if (count < max) {
    // 人数未满：让创建者先选总场次（服务端只对显式传入的场次做校验）
    await fetchMatchOptionsForStart()
    if (matchOptions.value.length === 0) {
      showToast('暂无可选的场次，请确认人数')
      return
    }
    showMatchPicker.value = true
    return
  }
  await withSubmitting(async () => {
    const res = await api.post(`/tournaments/${route.params.id}/start`)
    const actual = res.data?.total_matches ?? res.data?.matches
    if (actual != null && Number(actual) !== Number(tournament.value?.total_matches ?? actual)) {
      showToast(`所选场次无法排出，已按 ${actual} 场开赛`)
    } else {
      showToast('比赛已开始')
    }
    await fetchDetail()
  })
}
async function doDelete() {
  await withSubmitting(async () => {
    try { await showConfirmDialog({ title: '确认删除', message: '删除后不可恢复，确定要删除？' }) } catch { return }
    await api.delete(`/tournaments/${route.params.id}`)
    showToast('已删除')
    router.replace('/')
  })
}

async function doEndTournament() {
  await withSubmitting(async () => {
    try { await showConfirmDialog({ title: '确认结束', message: '提前结束赛事？结束后无法恢复。' }) } catch { return }
    await api.post(`/tournaments/${route.params.id}/end-tournament`)
    showToast('赛事已结束')
    await fetchDetail()
  })
}

async function onRefresh() {
  try {
    await refreshBoth(true)
  } catch {
    // 失败时保持现有内容，交给拦截器提示
  } finally {
    // 幂等重启：即使首屏加载失败导致定时器没建起来，下拉刷新也能救回来
    startCountdown()
    refreshing.value = false
  }
}

// 后台静默刷新：只在「影响本页」的广播上触发。
// 不能无条件刷新——每记一分都会广播 match_updated，那会让每场记分都空拉两个接口。
// 另外用时间窗合并「写操作成功后自己拉的那一次」紧随其后的广播刷新：
// 例如创建者点开始比赛，POST 成功后自己拉一次，广播到达又拉一次，白跑两个请求。
watch(lastMessage, (msg) => {
  const t = msg?.type
  if (t === 'registration_updated' || t === 'tournament_started' || t === 'tournament_finished') {
    refreshBothIfNotJustRefreshed()
  }
  // 取消记录弹层开着时跟着刷新：别人刚取消，这边列表要立刻变
  if (t === 'registration_updated' && showCancellations.value) {
    fetchCancellations().catch(() => {})
  }
})

// 锁屏/切后台回到前台时补一次刷新：本页虽订阅了 WebSocket，
// 但连接被冻结后既收不到 close 也不会立刻恢复（存活判定在 30s 心跳上做，
// 最坏要一个多心跳周期），期间赛事可能已被别人开始/结束，页面却还是旧状态。
useResumeRefresh(async () => {
  await Promise.all([fetchDetail(true), fetchRegistrations(true)])
})

onMounted(async () => {
  // 定时器与监听必须建在 await 之前：
  // 如果首次加载失败（网络抖动、5xx、超时），await 会抛出，
  // 后面的 startCountdown() 就永远不执行 —— 倒计时冻结在初始值、
  // openLocked 恒为 true，「立即报名」会永久禁用，只能刷新浏览器才恢复。
  startCountdown()
  document.addEventListener('visibilitychange', onVisibilityChange)
  try {
    await Promise.all([auth.fetchMe(), fetchDetail(), fetchRegistrations()])
  } catch {
    // 拦截器已弹错误提示；下拉刷新可恢复
  }
})
onUnmounted(() => {
  stopCountdown()
  document.removeEventListener('visibilitychange', onVisibilityChange)
  // 海报的 object URL 不释放会一直占着内存（与积分榜分享图同一处理）
  closePoster()
})
</script>

<style scoped>
.detail-page { display: flex; flex-direction: column; background: #f5f6f8; }
.detail-scroll { flex: 1; overflow-y: auto; }
.pull-fill { min-height: 100%; }
.pull-inner { padding-bottom: 60px; }
.stats-popup { overflow: hidden !important; }
.loading { display: flex; justify-content: center; margin-top: 100px; }
.load-failed {
  display: flex; flex-direction: column; align-items: center; gap: 6px;
  padding: 100px 24px 0; color: #969799; font-size: 14px; text-align: center;
}
.load-failed p { margin: 0; }
.load-failed-sub { font-size: 12px; color: #c8c9cc; margin-bottom: 8px !important; }
.info-card { margin: 10px 12px; padding: 16px; background: #fff; border-radius: 10px; }
.reg-countdown { text-align: center; color: #ff976a; font-size: 14px; margin-bottom: 10px; font-variant-numeric: tabular-nums; }
.info-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; }
.info-head h2 { font-size: 18px; font-weight: 700; }
.info-desc { color: #666; font-size: 13px; margin-bottom: 12px; }
.info-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px 12px; }
.ig-item { display: flex; align-items: center; gap: 4px; font-size: 12px; color: #888; }
.ig-item .van-icon { font-size: 13px; flex-shrink: 0; }
.action-block { padding: 10px 12px; }
.player-section { margin: 8px 12px; padding: 14px; background: #fff; border-radius: 10px; }
.player-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 10px; }
.ph-title { font-size: 15px; font-weight: 600; }
.ph-actions { display: flex; align-items: center; gap: 12px; }
.ph-action { display: inline-flex; align-items: center; gap: 2px; font-size: 12px; color: #1989fa; }
.player-grid { display: flex; flex-wrap: wrap; gap: 8px; }
.player-chip { display: flex; flex-direction: column; align-items: center; gap: 3px; width: 70px; cursor: pointer; }
.player-name { font-size: 12px; color: #666; text-align: center; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 64px; }
.avatar-badge-sm { position: relative; display: inline-block; }
/* inline-block 的头像下方会留出基线间隙（实测 4px），外层盒子因此比头像高 4px：
   角标锚的是上边（top 负值）**不受影响**，收益是头像与下方昵称之间不再多这段空隙。
   块级化即可消除，且不动行高（避免影响容器内其它文字） */
.avatar-badge-sm :deep(.van-image) { display: block; }
.host-badge {
  position: absolute; top: -2px; left: -2px;
  font-size: 8px; color: #fff; background: #e74c3c;
  padding: 0 3px; border-radius: 6px; line-height: 14px; white-space: nowrap;
}
.player-time { font-size: 10px; color: #bbb; }
.player-chip.more { justify-content: center; font-size: 13px; color: #999; cursor: default; }
.empty-hint { font-size: 13px; color: #ccc; text-align: center; padding: 10px 0; }
.nav-block { margin: 8px 12px; }
/* 导航栏右侧可能同时有「分享海报」和「删除」两个图标，拉开间距免得点错 */
.nav-right { display: inline-flex; align-items: center; gap: 14px; }
.creator-block { padding: 10px 12px; }
/* 滚动交给 .vh-sheet-body（main.css）：外壳 flex 列固定，内部只滚一次 */
.popup-content { padding: 20px; }
.popup-content h3 { margin-bottom: 14px; font-size: 16px; }
.popup-grid { display: flex; flex-wrap: wrap; gap: 10px; }
.popup-player { display: flex; flex-direction: column; align-items: center; gap: 4px; width: 68px; cursor: pointer; }
.popup-player span { font-size: 12px; color: #666; }
.popup-time { font-size: 10px !important; color: #bbb !important; }
.popup-player-head { display: flex; flex-direction: column; align-items: center; margin-bottom: 16px; }
/* 取消报名记录弹层 */
.cancel-body { padding: 4px 16px 16px; }
.cancel-row { display: flex; align-items: center; gap: 10px; padding: 10px 0; border-bottom: 1px solid #f5f5f5; }
.cancel-row:last-child { border-bottom: none; }
.cancel-name { flex: 1; font-size: 14px; color: #333; }
.cancel-kind {
  font-size: 11px; padding: 1px 6px; border-radius: 8px;
  color: #969799; background: #f2f3f5;
}
.cancel-kind.withdraw { color: #ed6a0c; background: #fff7e8; }
.cancel-time { font-size: 12px; color: #999; }
.transfer-player {
  display: flex; flex-direction: column; align-items: center; gap: 4px;
  width: 68px; cursor: pointer; padding: 6px; border-radius: 8px;
}
.transfer-player.selected { background: #e8f4ff; }
.transfer-player span { font-size: 12px; color: #666; }
.popup-player-head h3 { margin-top: 8px; }

.picker-toolbar { display: flex; align-items: center; justify-content: space-between; padding: 12px 16px; font-size: 15px; }
.picker-title { font-weight: 600; }
.van-cell.active { background: #e8f4ff; }
</style>
