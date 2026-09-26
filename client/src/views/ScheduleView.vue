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

      <van-empty v-if="visibleRounds.length === 0" description="已完成比赛已全部收起" />

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
import { hiddenFinishedIds, visibleRounds as buildVisibleRounds, focusMatchId, focusScrollTop } from '@/utils/schedule'
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

async function fetchRounds(skipLoading = false) {
  const res = await api.get(`/tournaments/${route.params.id}/rounds`, { skipLoading } as any)
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

async function claimReferee(m: any) {
  await api.post(`/tournaments/${route.params.id}/matches/${m.id}/claim-referee`)
  showToast('认领成功')
  // 认领后立即进入该场记分页，省掉用户再点一次
  router.push(`/tournament/${route.params.id}/score/${m.id}?num=${m.globalIdx}`)
}

const tid = Number(route.params.id)
const { lastMessage } = useWebSocket(tid)
// 后台静默刷新：实时广播触发，不弹全局「加载中...」
watch(lastMessage, (msg) => { if (msg?.type === 'match_updated') fetchRounds(true) })
// 锁屏/后台返回时补一次刷新（冻结期间 WebSocket 可能已断，不再收得到广播）
useResumeRefresh(() => fetchRounds(true))
async function onRefresh() {
  try { await fetchRounds(true) } finally { refreshing.value = false }
}
// --- 隐藏较早的已完成比赛 ---
// 默认只保留最后 1 场已完成的比赛，其余折叠；点击提示条可全部展开。
// 具体规则与编号逻辑见 utils/schedule.ts（纯函数，便于单测）。
const showAllFinished = ref(false)

const hiddenIds = computed(() => hiddenFinishedIds(rounds.value, showAllFinished.value))
const hiddenFinishedCount = computed(() => hiddenIds.value.size)
const hasHiddenFinished = computed(() => hiddenFinishedCount.value > 0 || showAllFinished.value)
const visibleRounds = computed(() => buildVisibleRounds(rounds.value, hiddenIds.value))
// 赛事已提前结束时，未打完的比赛也不允许再认领裁判 / 记分
const tournamentEnded = computed(() =>
  rounds.value.some((r: any) => (r.matches || []).some((m: any) => m.tournament_status === 'finished'))
)

// --- 进入页面时自动定位到当前该关注的比赛 ---
// 优先「进行中」的第一场；没有进行中的则取「待打」的第一场；
// 全部已结束（都折叠了）则不动。仅对本次进入页面生效，之后实时刷新不再抢占滚动位置。
const initialScrollDone = ref(false)
const targetMatchId = computed<number | null>(() => focusMatchId(rounds.value))
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
    // 注意：不能先 scrollIntoView 再读 getBoundingClientRect ——
    // scrollIntoView 的滚动不是立即可见的，随后读到的是滚动前的坐标，
    // 会造成二次滚动、落点偏低。改用 offsetTop 在容器坐标空间里一次算准。
    const scroller = el.closest('.schedule-scroll') as HTMLElement | null
    if (scroller) {
      const top = focusScrollTop(el.offsetTop, scroller.clientHeight)
      // 用 scrollTo 而非直接赋值 scrollTop：显式声明 behavior，避免继承
      // 任何全局 scroll-behavior: smooth 造成落点不精确
      scroller.scrollTo({ top, behavior: 'auto' })
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

onMounted(() => fetchRounds())
</script>

<style scoped>
.schedule-page { height: 100vh; display: flex; flex-direction: column; background: #f0f2f5; }
/* position: relative 让本容器成为卡片的 offsetParent，
   这样卡片的 offsetTop 才稳定等于「相对列表内容顶部」的偏移（自动定位依赖它） */
.schedule-scroll { flex: 1; overflow-y: auto; padding-top: 12px; position: relative; }
.pull-fill { min-height: 100%; }
.pull-inner { padding-bottom: 60px; }
.empty-block { padding-top: 80px; }
.round-section { margin: 0 12px; }

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
}

/* 首次进入时高亮落点比赛：明显描边保持 2.5s，移除后平滑淡出。
   这里只用 transition 不用 animation —— animation 播放期间会覆盖 transition
   对 box-shadow 的效果，导致淡出失效。
   必须排在 :active 之后：两者特异性相同，靠顺序决定优先级。 */
.match-card:active { box-shadow: 0 4px 12px rgba(0,0,0,.08); }

.match-card.target {
  box-shadow: 0 0 0 2px rgba(25, 137, 250, .6), 0 3px 14px rgba(25, 137, 250, .28);
  transition: box-shadow .5s ease-out;
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
