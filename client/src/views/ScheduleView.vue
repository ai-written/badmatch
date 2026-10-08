<template>
  <div class="schedule-page vh-page">
    <van-nav-bar title="对阵表" left-text="返回" left-arrow @click-left="goBack">
      <template #right>
        <span class="nav-link" @click="$router.push(`/tournament/${route.params.id}/rankings`)">积分榜</span>
      </template>
    </van-nav-bar>

    <div class="schedule-scroll">
    <van-pull-refresh v-model="refreshing" @refresh="onRefresh" class="pull-fill">
      <div class="pull-inner">
    <div v-if="rounds.length === 0 && loadFailed" class="empty-block">
      <div class="load-failed">
        <van-icon name="warning-o" size="22" />
        <span>赛程加载失败</span>
        <span class="load-failed-sub">赛事可能已被删除，或网络异常</span>
        <van-button size="small" round plain type="primary" @click="onRefresh">重试</van-button>
      </div>
    </div>
    <div v-else-if="rounds.length === 0" class="empty-block">
      <van-empty description="暂无赛程" />
    </div>

    <template v-else>
      <!-- 按人筛选：只看某些人（可多选）/ 排除一个或多个人（可多选，可叠加）。条件会记住（见下方 FILTER_KEY） -->
      <div class="filter-bar">
        <div class="filter-chip" :class="{ on: filterActive }" @click="openFilter">
          <van-icon name="filter-o" size="14" />
          <span class="filter-text">{{ filterSummary }}</span>
          <van-icon name="arrow-down" size="10" />
        </div>
        <span v-if="myPlayerId != null" class="filter-mini"
              :class="{ on: onlyMeActive }" @click="toggleOnlyMe">只看我</span>
        <span v-if="filterActive" class="filter-mini clear" @click="clearFilter">清除</span>
      </div>

      <!-- 赛事已提前结束：所有比赛只读 -->
      <div v-if="tournamentEnded" class="readonly-banner">
        <van-icon name="lock" />
        <span>赛事已结束，赛程与比分已锁定</span>
      </div>
      <!-- 默认折叠较早的已完成比赛，只留最近 1 场；点击展开查看全部 -->
      <div v-if="hasHiddenFinished" class="hidden-toggle" @click="showAllFinished = !showAllFinished">
        <van-icon :name="showAllFinished ? 'arrow-up' : 'arrow-down'" />
        <span v-if="showAllFinished">收起较早的已完成比赛</span>
        <span v-else>已隐藏 {{ hiddenFinishedCount }} 场已完成比赛</span>
      </div>

      <van-empty v-if="visibleRounds.length === 0 && filterActive" description="没有符合筛选条件的比赛">
        <van-button size="small" round plain type="primary" @click="clearFilter">清除筛选</van-button>
      </van-empty>
      <van-empty v-else-if="visibleRounds.length === 0" description="已完成比赛已全部收起" />

      <template v-for="r in visibleRounds" :key="r.id">
        <div class="round-section">
          <div
            v-for="m in r.matches"
            :key="m.id"
            :ref="(el: any) => { if (m.id === targetMatchId) targetCard = el as HTMLElement }"
            class="match-card"
            :class="{ my: isMyMatch(m), target: m.id === highlightMatchId }"
            @click="goScore(m)"
          >
          <div class="match-row">
            <div class="match-side" :class="{ win: m.winner_pairing_id === m.pairing_a.id }">
              <div class="player-block" :class="{ win: m.winner_pairing_id === m.pairing_a.id }">
                <div class="avatar-badge">
                  <van-image lazy-load round width="36" height="36" :src="m.pairing_a.player_a.avatar || defaultAvatar" />
                  <span v-if="m.pairing_a.player_a.id === auth.user?.id" class="me-badge">我</span>
                  <span class="badge-icon" v-if="m.status === 'finished' && m.winner_pairing_id === m.pairing_a.id">🏆</span>
                </div>
                <span class="player-name">{{ m.pairing_a.player_a.username }}</span>
              </div>
              <div class="player-block" :class="{ win: m.winner_pairing_id === m.pairing_a.id }">
                <div class="avatar-badge">
                  <van-image lazy-load round width="36" height="36" :src="m.pairing_a.player_b.avatar || defaultAvatar" />
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
                  <van-image lazy-load round width="36" height="36" :src="m.pairing_b.player_a.avatar || defaultAvatar" />
                  <span v-if="m.pairing_b.player_a.id === auth.user?.id" class="me-badge">我</span>
                  <span class="badge-icon" v-if="m.status === 'finished' && m.winner_pairing_id === m.pairing_b.id">🏆</span>
                </div>
                <span class="player-name">{{ m.pairing_b.player_a.username }}</span>
              </div>
              <div class="player-block" :class="{ win: m.winner_pairing_id === m.pairing_b.id }">
                <div class="avatar-badge">
                  <van-image lazy-load round width="36" height="36" :src="m.pairing_b.player_b.avatar || defaultAvatar" />
                  <span v-if="m.pairing_b.player_b.id === auth.user?.id" class="me-badge">我</span>
                  <span class="badge-icon" v-if="m.status === 'finished' && m.winner_pairing_id === m.pairing_b.id">🏆</span>
                </div>
                <span class="player-name">{{ m.pairing_b.player_b.username }}</span>
              </div>
            </div>

            <div class="match-info">
              <span class="match-num">第{{ m.globalIdx }}场</span>
              <span v-if="m.status === 'finished' && m.duration_seconds != null" class="match-dur">{{ fmtDuration(m.duration_seconds) }}</span>
              <span v-if="m.referee" class="foot-ref has">裁 {{ m.referee.username }}<template v-if="!m.active_referee">（已卸任）</template></span>
              <van-button v-if="m.can_referee" size="mini" type="primary" round @click.stop="claimReferee(m)">裁判</van-button>
            </div>
          </div>
        </div>
      </div>
      </template>
    </template>
      </div>
    </van-pull-refresh>
    </div>

    <!-- 筛选面板：面板里改动的是草稿，点「确定」才生效（避免边点边变、误触后回不去） -->
    <van-popup v-model:show="showFilter" position="bottom" round class="vh-sheet vh-60">
      <div class="filter-panel-head">
        <span class="filter-panel-title">筛选比赛</span>
        <van-icon name="cross" size="18" @click="showFilter = false" />
      </div>
      <div class="vh-sheet-body filter-panel-body">
        <div class="filter-label">只看某人的比赛（可多选）</div>
        <!-- 多选匹配方式：默认「或」（任意一人），可切成「且」（必须同场）。
             刻意不用 .pick-row 这个类名：e2e 是按 .pick-row 的下标定位选手行的（0=只看、1=排除） -->
        <div class="mode-row">
          <span class="pick" :class="{ on: draftMode === 'any' }" @click="draftMode = 'any'">或（任意一人）</span>
          <span class="pick" :class="{ on: draftMode === 'all' }" @click="draftMode = 'all'">且（必须同场）</span>
        </div>
        <div class="pick-row">
          <span class="pick" :class="{ on: draftOnlyIds.length === 0 }" @click="clearDraftOnly()">全部</span>
          <span v-for="p in playerOptions" :key="p.id" class="pick"
                :class="{ on: draftOnlyIds.includes(p.id) }" @click="toggleDraftOnly(p.id)">{{ p.username }}</span>
        </div>
        <div v-if="draftMode === 'all' && draftOnlyIds.length > 4" class="filter-hint warn">
          「且」要求所选的人出现在同一场里，而 2v2 一场只有 4 人 —— 现在选了 {{ draftOnlyIds.length }} 人，
          筛不出任何比赛。
        </div>

        <div class="filter-label">排除（可多选）</div>
        <div class="pick-row">
          <span v-for="p in playerOptions" :key="p.id" class="pick"
                :class="{ on: draftExclude.includes(p.id), muted: draftOnlyIds.includes(p.id) }"
                @click="toggleDraftExclude(p.id)">{{ p.username }}</span>
        </div>
        <div class="filter-hint">
          「或」= 选中的人里任意一位参加的比赛（默认）；「且」= 这些人必须出现在同一场里（2v2 一场只有 4 人）。
          排除是并集：排除谁，谁参加的比赛就都不显示。两者可叠加：
          例如「只看张三、李四」并「排除王五」= 张三或李四参加、且王五不参加的比赛。
        </div>
      </div>
      <div class="filter-actions">
        <van-button round plain block @click="resetDraft">重置</van-button>
        <van-button round type="primary" block @click="applyFilter">确定</van-button>
      </div>
    </van-popup>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, watch, nextTick } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import api from '@/api/client'
import { useWebSocket } from '@/composables/useWebSocket'
import { useResumeRefresh } from '@/composables/useResumeRefresh'
import { useGoBack } from '@/composables/useGoBack'
import {
  withGlobalIndex, hiddenFinishedIds, renderRounds, filterRounds, playersInRounds,
  normalizeFilter, isFilterActive, focusMatchId, focusScrollTop,
  pruneFilter, isOnlyMeFilter, nextOnlyMeFilter,
  emptyFilter, type ScheduleFilter, type OnlyMode,
} from '@/utils/schedule'
import { showToast, showConfirmDialog } from 'vant'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const { goBack } = useGoBack()
const refreshing = ref(false)
const rounds = ref<any[]>([])
// 加载失败（赛事不存在/网络异常）与「暂无赛程」必须区分开
const loadFailed = ref(false)
// 已成功加载过赛程的**赛事 id**（不是布尔）：护栏按赛事记，这样将来即使同组件被
// 复用到另一个赛事（路由未重建组件），也不会拿"新赛事的名单"去剪"旧赛事的条件"
const roundsLoadedFor = ref<number | null>(null)
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

async function fetchRounds(skipLoading = false) {
  try {
    const res = await api.get(`/tournaments/${route.params.id}/rounds`, { skipLoading } as any)
    rounds.value = res.data
    loadFailed.value = false
    // 记下"这个赛事的赛程已成功加载过"，剪枝护栏用（见 roundsLoadedFor 的说明）
    roundsLoadedFor.value = tid
  } catch (e) {
    // 赛事不存在（后端现在返回 404）或网络异常：
    // 不能让它显示成「暂无赛程」——那是「赛事还没有比赛」的意思，两回事
    if (rounds.value.length === 0) loadFailed.value = true
    throw e
  }
}

function goScore(m: any) {
  if (!auth.token) {
    sessionStorage.setItem('loginRedirect', `/tournament/${route.params.id}/schedule`)
    router.push('/profile')
    return
  }
  router.push(`/tournament/${route.params.id}/score/${m.id}?num=${m.globalIdx}`)
}

async function claimReferee(m: any) {
  // 本场已有在任裁判时先确认：认领会**顶替**对方（用于裁判临时有事要转让，
  // 或原裁判手机没电了借别人的手机代认领）。后端允许顶替，这里只做提醒。
  const cur = m.active_referee
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
  await api.post(`/tournaments/${route.params.id}/matches/${m.id}/claim-referee`)
  showToast('认领成功')
  // 认领后立即进入该场记分页，省掉用户再点一次
  router.push(`/tournament/${route.params.id}/score/${m.id}?num=${m.globalIdx}`)
}

const tid = Number(route.params.id)
const { lastMessage } = useWebSocket(tid)
// 后台静默刷新：实时广播触发，不弹全局「加载中...」。
// referee_claimed / referee_released 同样要跟：认领、卸任、以及「被顶替」都会改变
// 卡片上的「裁 X」与「裁判」按钮状态，不接的话别人认领完你这边还是旧样子。
watch(lastMessage, (msg) => {
  const t = msg?.type
  // registration_updated：有人退赛会**重排赛程**（未开打的轮次被删、重新生成），
  //   不重拉的话列表与筛选条件都停在旧快照上（「已不在赛程里的人」也剪不掉）；
  // tournament_finished：比赛全部打完自动结束时后端只发这一个事件，
  //   少了它就看不到「赛事已结束」的只读横幅。
  if (t === 'match_updated' || t === 'referee_claimed' || t === 'referee_released'
      || t === 'registration_updated' || t === 'tournament_finished') {
    fetchRounds(true).catch(() => {})
  }
})
// 锁屏/后台返回时补一次刷新（冻结期间 WebSocket 可能已断，不再收得到广播）
useResumeRefresh(async () => { await fetchRounds(true) })
async function onRefresh() {
  try {
    await fetchRounds(true)
  } catch {
    // 失败态已由 fetchRounds 置位；拦截器也已提示
  } finally {
    refreshing.value = false
  }
}
// --- 隐藏较早的已完成比赛 ---
// 默认只保留最后 1 场已完成的比赛，其余折叠；点击提示条可全部展开。
// 具体规则与编号逻辑见 utils/schedule.ts（纯函数，便于单测）。
const showAllFinished = ref(false)

// 赛事是否已结束（提前结束也算）。必须与「折叠」用同一套判断：
// 否则手动提前结束时会出现「顶部说已锁定、列表却还在折叠」的自相矛盾
const tournamentEnded = computed(() =>
  rounds.value.some((r: any) => (r.matches || []).some((m: any) => m.tournament_status === 'finished'))
)

// --- 按人筛选：只看某些人（可多选）/ 排除一个或多个人（可多选） ---
// 条件按赛事记住（同一台手机上回到这个赛事时还在），因此状态串存在 localStorage 里。
// 升级前的单选格式由 normalizeFilter 兼容读取（见 utils/schedule.ts）。
const FILTER_KEY = `schedule-filter:${route.params.id}`
function loadFilter(): ScheduleFilter {
  let parsed: unknown = null
  try {
    const raw = localStorage.getItem(FILTER_KEY)
    if (!raw) return emptyFilter()
    parsed = JSON.parse(raw)
  } catch {
    // 只有「读/解析失败」才当作坏键清掉：被外部写坏或更早版本的残留，
    // 不清掉的话每次打开都要再失败一次。
    // 注意 emptyFilter() 不能换成共享常量：那样 excludePlayerIds 数组会被各方共用。
    try { localStorage.removeItem(FILTER_KEY) } catch { /* 隐私模式下删不掉，只影响「记住筛选」 */ }
    return emptyFilter()
  }
  const f = normalizeFilter(parsed)
  // 旧格式迁移的回写**单独 try**：写不进去（Safari 无痕、配额满）只影响「记住筛选」，
  // 绝不能把刚刚已经读出来的合法条件一起丢掉 —— 那会让用户的条件静默消失。
  // 这里只做规范化、**不按赛程剪枝**：此刻 rounds 还没加载、playerOptions 是空的，
  // 剪枝会把用户存的条件整个清空（剪枝交给 playerOptions 的 watch）。
  try {
    if (JSON.stringify(f) !== JSON.stringify(parsed)) {
      if (isFilterActive(f)) localStorage.setItem(FILTER_KEY, JSON.stringify(f))
      else localStorage.removeItem(FILTER_KEY)
    }
  } catch { /* 写不进去不影响本次读取到的条件 */ }
  return f
}
const activeFilter = ref<ScheduleFilter>(loadFilter())
const filterActive = computed(() => isFilterActive(activeFilter.value))
// 候选项取自**全量**赛程：若取自筛选结果，选中「只看张三」后其他人就从列表里消失了，
// 再也切不回去。
const playerOptions = computed(() => playersInRounds(rounds.value))
const myPlayerId = computed(() => {
  const uid = auth.user?.id
  return uid != null && playerOptions.value.some((p) => p.id === uid) ? uid : null
})
// 「只看我」是否正处于生效状态（判定逻辑在 utils/schedule.ts，有单测）：
// 条件正好是「只看我一个人」才算生效 —— 不能只看「我在不在选中集合里」。
const onlyMeActive = computed(() => isOnlyMeFilter(activeFilter.value, myPlayerId.value))
// 赛中退赛会重排赛程（见 server 的 withdraw），被选中的那个人从此不再出现在任何一场比赛里：
// 他既不可能被筛出来，面板里也没有可点的 chip，条件会永远卡在集合里（摘要显示成 `#123`）。
// 因此赛程加载出来后就把这类 id 剪掉。两道护栏：
//   1. 只在这个赛事的赛程**成功加载过**之后才剪 —— 拉取失败时 rounds 也是空的，
//      这时剪会把用户存的条件当成"人已不在赛程"而清空；
//   2. 赛程里一个人都没有时不剪 —— 没有可比对的名单，而且此时筛选栏根本不渲染，
//      条件留着无害、等赛程回来还能自动生效（宁可暂时残留，也不要静默删掉用户的条件）。
// 剪枝规则本身在 utils/schedule.ts（有单测），这里只负责"什么时候剪"。
function pruneMissingIds(f: ScheduleFilter): ScheduleFilter {
  if (roundsLoadedFor.value !== tid || playerOptions.value.length === 0) return f
  return pruneFilter(f, playerOptions.value.map((p) => p.id))
}
// playerOptions 每次赛程刷新都会重新计算（含 WebSocket 广播触发的静默刷新），
// 因此这一个 watch 同时覆盖「刚进页面加载完」和「比赛进行中有人退赛」两种情况。
watch(playerOptions, () => {
  const next = pruneMissingIds(activeFilter.value)
  if (next === activeFilter.value) return   // 没变化（含"没加载好/零场次"）就什么都不做
  activeFilter.value = next
  persistFilter()
  // 剪枝是破坏性的（会覆盖 localStorage），不能静默：告诉用户条件被收窄了
  const kept = activeFilter.value.onlyPlayerIds.length + activeFilter.value.excludePlayerIds.length
  showToast(kept > 0
    ? `已移除不在赛程中的筛选条件（还剩 ${kept} 人）`
    : '筛选条件里的人已不在赛程，已自动清除')
  // 面板正开着时，草稿也要跟着剪：否则草稿里留着一个面板上没有 chip 的 id，
  // 会让「全部」chip 也不高亮，看起来"什么都没选"（点确定虽然会自愈，但视觉上自相矛盾）
  const ids = new Set(playerOptions.value.map((p) => p.id))
  draftOnlyIds.value = draftOnlyIds.value.filter((id) => ids.has(id))
  draftExclude.value = draftExclude.value.filter((id) => ids.has(id))
})
function nameOf(id: number) {
  return playerOptions.value.find((p) => p.id === id)?.username || `#${id}`
}
/** 按面板（赛程里首次出场）的顺序排列 id。
 *  条件里存的是点选顺序，直接用会让摘要里的名字忽前忽后、与面板里的排列对不上；
 *  这只影响筛选栏那行文字，不涉及比赛列表的顺序（那份由赛程决定，见 filterRounds）。 */
function sortByPanelOrder(ids: number[]): number[] {
  const order = new Map(playerOptions.value.map((p, i) => [p.id, i]))
  return [...ids].sort((a, b) =>
    (order.get(a) ?? Number.MAX_SAFE_INTEGER) - (order.get(b) ?? Number.MAX_SAFE_INTEGER))
}
const filterSummary = computed(() => {
  const f = activeFilter.value
  if (!filterActive.value) return '全部比赛'
  const only = sortByPanelOrder(f.onlyPlayerIds)
  if (only.length === 0) return `已排除 ${f.excludePlayerIds.length} 人`
  // 2 人以内全列；3 人以上只列第一个 + 总数：筛选栏固定一行，长名单会被省略号截断，
  // 截断之后连「一共选了几个人」都看不出来（见 .filter-text 的 ellipsis）
  const onlyText = only.length <= 2
    ? only.map(nameOf).join('、')
    : `${nameOf(only[0])} 等 ${only.length} 人`
  // 「且」模式用「同场」开头：一眼能看出这次筛的是"必须一起打"，而不是"任一人"
  const prefix = f.onlyMode === 'all' ? '同场' : '只看'
  if (f.excludePlayerIds.length === 0) return `${prefix} ${onlyText}`
  return `${prefix} ${onlyText} · 排除 ${f.excludePlayerIds.length} 人`
})

const showFilter = ref(false)
const draftOnlyIds = ref<number[]>([...activeFilter.value.onlyPlayerIds])
const draftExclude = ref<number[]>([...activeFilter.value.excludePlayerIds])
const draftMode = ref<OnlyMode>(activeFilter.value.onlyMode)
function openFilter() {
  draftOnlyIds.value = [...activeFilter.value.onlyPlayerIds]
  draftExclude.value = [...activeFilter.value.excludePlayerIds]
  draftMode.value = activeFilter.value.onlyMode
  showFilter.value = true
}
// 「只看」多选：点一下选中/取消。选中时自动从排除里去掉 —— 同一人既「只看」又「排除」
// 结果必然为空（自相矛盾），面板里不该让用户凑出这种条件
function toggleDraftOnly(id: number) {
  const selected = draftOnlyIds.value.includes(id)
  draftOnlyIds.value = selected
    ? draftOnlyIds.value.filter((x) => x !== id)
    : [...draftOnlyIds.value, id]
  if (!selected) draftExclude.value = draftExclude.value.filter((x) => x !== id)
}
function clearDraftOnly() {
  draftOnlyIds.value = []
}
function toggleDraftExclude(id: number) {
  // 已在「只看」里的人不能被排除（两者叠加的结果必然为空）：
  // 光把 chip 置灰、点了没反应会让人以为界面卡了，这里明确说一句。
  if (draftOnlyIds.value.includes(id)) {
    showToast(`「${nameOf(id)}」已在「只看」里，不能再排除`)
    return
  }
  draftExclude.value = draftExclude.value.includes(id)
    ? draftExclude.value.filter((x) => x !== id)
    : [...draftExclude.value, id]
}
function resetDraft() {
  draftOnlyIds.value = []
  draftExclude.value = []
  draftMode.value = 'any'
}
function persistFilter() {
  try {
    // 没有条件就把键删掉，别在 localStorage 里留一个空壳条目（按赛事累积会越来越多）
    if (!isFilterActive(activeFilter.value)) {
      localStorage.removeItem(FILTER_KEY)
      return
    }
    localStorage.setItem(FILTER_KEY, JSON.stringify(activeFilter.value))
  } catch {
    // 隐私模式/被禁用时写不进去：只影响「记住筛选」，不该让页面报错
  }
}
function applyFilter() {
  // 再过一次剪枝：面板打开期间可能正好有人退赛（草稿里还留着他的 chip），
  // 不剪的话刚点「确定」就会把一个已不在赛程里的 id 写回条件
  activeFilter.value = pruneMissingIds(
    normalizeFilter({
      onlyPlayerIds: draftOnlyIds.value,
      excludePlayerIds: draftExclude.value,
      onlyMode: draftMode.value,
    }),
  )
  persistFilter()
  showFilter.value = false
}
function clearFilter() {
  activeFilter.value = emptyFilter()
  resetDraft()
  persistFilter()
}
function toggleOnlyMe() {
  const uid = myPlayerId.value
  if (uid == null) return
  // 「只看我」是**一键收敛**，不是往多选集合里加自己：不管当前选了多少人、排除了谁，
  // 点它都变成「只看我一个人」。排除必须一起清掉 —— 否则被别人挡住的、我自己参加的比赛
  // 仍然看不到，与按钮的字面意思不符。
  // 已经正好是「只看我一个人」时再点一下 = 取消筛选，回到全部比赛（保留开关键手感）。
  // 状态迁移是纯函数（utils/schedule.ts 的 nextOnlyMeFilter），有单测。
  activeFilter.value = nextOnlyMeFilter(activeFilter.value, uid)
  persistFilter()
}

// 处理顺序（不能颠倒）：
//   1. 先在**全量**赛程上编号 —— 筛选不改变「第N场」，同一场在不同筛选下编号一致
//   2. 再按人裁剪 —— 折叠规则只针对筛完剩下的比赛（只看某人时也能折叠他的旧战绩）
//   3. 最后折叠 + 丢掉空轮次
const numberedRounds = computed(() => withGlobalIndex(rounds.value))
const filteredRounds = computed(() => filterRounds(numberedRounds.value, activeFilter.value))
const hiddenIds = computed(() => hiddenFinishedIds(filteredRounds.value, showAllFinished.value, tournamentEnded.value))
const hiddenFinishedCount = computed(() => hiddenIds.value.size)
const hasHiddenFinished = computed(() => hiddenFinishedCount.value > 0 || showAllFinished.value)
const visibleRounds = computed(() => renderRounds(filteredRounds.value, hiddenIds.value))

// --- 进入页面时自动定位到当前该关注的比赛 ---
// 优先「进行中」的第一场；没有进行中的则取「待打」的第一场；
// 全部已结束（都折叠了）则不动。仅对本次进入页面生效，之后实时刷新不再抢占滚动位置。
// 落点在**筛选后**的比赛里取：只看某人时，别把视口滚到他根本不参加的场次上。
const initialScrollDone = ref(false)
const targetMatchId = computed<number | null>(() => focusMatchId(filteredRounds.value))
const targetCard = ref<HTMLElement | null>(null)
// 高亮不能跟着 initialScrollDone 一起消失：批处理会在首次绘制前就把它移除，等于从未显示。
// 因此单独用一个 id 记录「本次落点」，并在若干帧后由定时器清除，让高亮真正可见一段时间。
const highlightMatchId = ref<number | null>(null)
let highlightTimer: ReturnType<typeof setTimeout> | null = null
const HIGHLIGHT_MS = 2500

watch(
  [targetMatchId, visibleRounds],
  async () => {
    if (initialScrollDone.value || targetMatchId.value == null) return
    await nextTick()
    const el = targetCard.value
    // 目标比赛可能仍被折叠（例如还没打完却排在已完成的之后），此时不滚动
    if (!el) return
    initialScrollDone.value = true
    highlightMatchId.value = targetMatchId.value
    // 目标比赛落在可视区偏上约 1/3 处：上方保留一点上文（前面打到哪），
    // 下方留出更多空间展示后续比赛，比纯居中更实用。
    // 注意：不能先 scrollIntoView 再读坐标 —— scrollIntoView 的滚动不是立即可见的，
    // 随后读到的是滚动前的值，会造成二次滚动、落点偏低。这里一次算准。
    const scroller = el.closest('.schedule-scroll') as HTMLElement | null
    if (scroller) {
      // 用 rect 差值换算，而不是 el.offsetTop：
      // Vant 的 .van-pull-refresh__track 自带 position:relative，它才是真正的
      // offsetParent，所以 offsetTop 的基准与 scroller.scrollTop 的原点并不一致
      // （会差 .schedule-scroll 的 padding-top）。rect 差值不受此影响。
      const offsetInScroller =
        el.getBoundingClientRect().top - scroller.getBoundingClientRect().top
        - scroller.clientTop + scroller.scrollTop
      const top = focusScrollTop(offsetInScroller, scroller.clientHeight)
      // 显式 instant：behavior:'auto' 表示「沿用 CSS scroll-behavior」，
      // 若将来有全局 smooth 就不精确了
      scroller.scrollTo({ top, behavior: 'instant' as ScrollBehavior })
    } else {
      // 兜底：容器结构变化时退化为居中
      el.scrollIntoView({ block: 'center' })
    }
    if (highlightTimer) clearTimeout(highlightTimer)
    highlightTimer = setTimeout(() => {
      highlightMatchId.value = null
      highlightTimer = null
    }, HIGHLIGHT_MS)
  },
  { flush: 'post' },
)

onUnmounted(() => {
  if (highlightTimer) clearTimeout(highlightTimer)
})

onMounted(() => {
  fetchRounds().catch(() => {})
  // 直接打开/刷新本页时 store 里还没有 user（fetchMe 由我的/详情/记分/积分榜各页自己调，
  // 本页原先漏了），而「我」角标、卡片 .my 高亮、以及筛选栏的「只看我」都依赖它 ——
  // 表现为分享出去的赛程链接打开后看不到自己的比赛。
  auth.fetchMe(true).catch(() => {})
})
</script>

<style scoped>
.schedule-page { display: flex; flex-direction: column; background: #f0f2f5; }
/* position: relative 让本容器成为卡片的 offsetParent，
   这样卡片的 offsetTop 才稳定等于「相对列表内容顶部」的偏移（自动定位依赖它） */
/* 这 12px 是 main.css 里 .van-pull-refresh 那套「padding + 负 margin」的落点：
   刷新容器的盒子会整体上移，滚动容器得留出同样的空间，否则上移的那截会被滚动容器
   自己裁掉。12px 同时覆盖了首条高亮上方所需的 11px（描边 2px + 光晕 14-3）。 */
.schedule-scroll { flex: 1; overflow-y: auto; position: relative; padding-top: 12px; }
.pull-fill { min-height: 100%; }
/* 顶部余量由 main.css 的 .van-pull-refresh（裁剪框）+ 上面那个滚动容器的 padding 负责，
   内容本身不动 —— 详见 main.css 里的说明 */
.pull-inner { padding-bottom: 60px; }
.empty-block { padding-top: 80px; }
.load-failed {
  display: flex; flex-direction: column; align-items: center; gap: 6px;
  color: #969799; font-size: 14px; text-align: center;
}
.load-failed-sub { font-size: 12px; color: #c8c9cc; margin-bottom: 8px; }
.round-section { margin: 0 12px; }

/* 导航栏右侧的积分榜入口 */
.nav-link { font-size: 14px; color: #1989fa; }

/* --- 按人筛选 --- */
/* 筛选栏是滚动内容的第一块：顶部留白仍由 .schedule-scroll 的 padding-top 负责，
   这里不能再加 margin-top，否则整页首元素位置会变（e2e 有像素基线） */
.filter-bar { display: flex; align-items: center; gap: 8px; margin: 0 12px 12px; }
.filter-chip {
  flex: 1; min-width: 0;
  display: flex; align-items: center; gap: 5px;
  padding: 8px 12px; border-radius: 10px;
  background: #fff; color: #646566; font-size: 13px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, .04);
}
.filter-chip.on { color: #1989fa; font-weight: 500; }
.filter-chip:active { background: #f7f8fa; }
/* min-width:0 + 省略号：排除了多个长名字时不能把这一行撑破 */
.filter-text { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.filter-mini {
  flex-shrink: 0; padding: 8px 12px; border-radius: 10px;
  background: #fff; color: #646566; font-size: 12px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, .04);
}
.filter-mini.on { background: #e8f3ff; color: #1989fa; font-weight: 500; }
.filter-mini.clear { color: #969799; }
.filter-mini:active { opacity: .7; }

.filter-panel-head {
  display: flex; align-items: center; justify-content: space-between;
  padding: 14px 16px 6px; flex-shrink: 0;
}
.filter-panel-title { font-size: 16px; font-weight: 600; color: #323233; }
.filter-panel-body { padding: 0 16px; }
.filter-label { margin: 14px 0 8px; font-size: 13px; color: #969799; }
.filter-label:first-child { margin-top: 6px; }
.pick-row { display: flex; flex-wrap: wrap; gap: 8px; }
.pick {
  max-width: 132px; padding: 6px 12px; border-radius: 16px;
  background: #f5f6f8; color: #646566; font-size: 13px;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.pick.on { background: #1989fa; color: #fff; }
/* 已选为「只看」的人：排除它没有意义，置灰且点不动 */
.pick.muted { opacity: .35; }
.filter-hint { margin: 16px 0 4px; font-size: 12px; color: #c8c9cc; line-height: 1.5; }
/* 「且（同场）」选超过 4 人时不可能有结果，用暖色明确提示，而不是让用户对着空列表猜 */
.filter-hint.warn { margin: 8px 0 4px; color: #ee0a24; }
/* 匹配方式那一行紧跟在标题下，和选手 chip 行稍作区分。
   故意不叫 .pick-row：e2e 用 .pick-row 的下标记位选手行 */
.mode-row { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 10px; }
.filter-actions {
  display: flex; gap: 10px; flex-shrink: 0;
  padding: 12px 16px calc(16px + env(safe-area-inset-bottom));
}

/* 赛事已结束的只读提示 */
.readonly-banner {
  display: flex; align-items: center; justify-content: center; gap: 6px;
  margin: 0 12px 12px; padding: 10px 12px;
  font-size: 13px; color: #8a6d3b;
  background: #fdf6e3; border: 1px solid #f5e2b8; border-radius: 10px;
}

/* 折叠提示条：不明显抢戏，但可点击区域要够大（移动端） */
.hidden-toggle {
  display: flex; align-items: center; justify-content: center; gap: 4px;
  margin: 0 12px 12px; padding: 9px 12px;
  font-size: 12px; color: #969799;
  background: #fff; border-radius: 10px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, .04);
}
.hidden-toggle:active { background: #f7f8fa; }


.match-card {
  background: #fff; border-radius: 12px; padding: 12px 14px; margin-bottom: 10px;
  box-shadow: 0 2px 8px rgba(0,0,0,.04); position: relative;
  /* transition 必须在基类上：若只写在 .target 上，类移除后新的计算值里
     已经没有 transition，高亮会瞬间复位而不是淡出 */
  transition: box-shadow .5s ease-out;
}

/* 首次进入时高亮落点比赛：明显描边保持 2.5s，移除后由基类的 transition 平滑淡出。
   必须排在 :active 之后：两者特异性相同，靠顺序决定优先级。 */
.match-card:active { box-shadow: 0 4px 12px rgba(0,0,0,.08); }

.match-card.target {
  box-shadow: 0 0 0 2px rgba(25, 137, 250, .6), 0 3px 14px rgba(25, 137, 250, .28);
}

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
/* 同 TournamentDetail：消除 inline-block 头像下方那 4px 基线间隙
   （角标锚在上边、不受影响，收益是头像与下方名字之间不再多一段空隙） */
.avatar-badge :deep(.van-image) { display: block; }
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
