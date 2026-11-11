<template>
  <div class="rank-page vh-page">
    <div class="rank-scroll">
    <van-pull-refresh v-model="refreshing" @refresh="onRefresh" class="pull-fill">
      <div class="pull-inner">
        <!-- 头图跟着内容滚：标题/副标题在渐变上，表格在下面的白色浮层里 -->
        <AppHero :height="156">
          <div class="rank-nav">
            <button class="nav-back" @click="goBack">‹ 返回</button>
            <span class="nav-title">积分榜</span>
            <van-icon name="share-o" size="19" class="share-btn" @click="openShare" />
          </div>
          <div class="hero-title rank-name">{{ ranking?.tournament_title || '积分榜' }}</div>
          <div class="hero-sub rank-kind">🏆 双打胜率</div>
        </AppHero>

        <div class="rank-sheet">
          <van-loading v-if="!ranking" class="loading" />
          <template v-else>
            <div class="rank-card">
              <div class="rank-head">
                <span>排名</span><span>队员</span><span>比分</span><span>净胜</span><span>胜率</span>
              </div>
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
                  <van-image lazy-load round width="26" height="26" :src="p.avatar || defaultAvatar" />
                  <span class="p-name">{{ p.username }}</span>
                </div>
                <!-- 胜/负两个数字各自可点：看「我赢/输的那几场，和谁搭挡、对手是谁」。
                     0 场的那个数字不可点（点开只会是空列表） -->
                <span class="col-wl">
                  <em
                    class="wl-win"
                    :class="{ tappable: canOpen(p) }"
                    @click.stop="openMatches(p, 'win')"
                  >{{ p.matches_won }}</em><span class="wl-dash">-</span><em
                    class="wl-lose"
                    :class="{ tappable: canOpen(p) }"
                    @click.stop="openMatches(p, 'lose')"
                  >{{ p.matches_lost }}</em>
                </span>
                <span class="col-diff" :class="p.point_diff >= 0 ? 'positive' : 'negative'">{{ p.point_diff > 0 ? '+' : '' }}{{ p.point_diff }}</span>
                <span class="col-rate">{{ winRate(p) }}%</span>
              </div>
            </div>
          </template>
        </div>
      </div>
    </van-pull-refresh>
    </div>

    <!-- 点胜/负数字：这一档场次的明细（搭挡、对手、比分、第N场、场地时间）。
         数据复用 /rounds（一次懒加载后本地筛），不额外加接口 -->
    <van-popup v-model:show="showMatches" round position="bottom" class="vh-sheet vh-65" lock-scroll>
      <div class="picker-toolbar">
        <span @click="showMatches = false">关闭</span>
        <span class="picker-title">{{ matchTitle }}</span>
      </div>
      <div class="vh-sheet-body match-body">
        <van-loading v-if="matchesLoading" class="sheet-loading" />
        <div v-else-if="matchesFailed" class="match-empty" @click="loadRounds(true)">
          加载失败，点这里重试
        </div>
        <div v-else-if="!rankDetail.rows.length" class="match-empty">
          还没有{{ matchResult === 'win' ? '赢' : '输' }}过的比赛
        </div>        <div v-else class="match-list">
          <div v-for="row in rankDetail.rows" :key="row.matchId" class="match-row">
            <span class="m-no">第{{ row.no }}场</span>
            <span class="m-score" :class="row.won ? 'won' : 'lost'">{{ row.score }}</span>
            <span class="m-detail">
              <span class="m-partner">搭挡 {{ row.partner }}</span>
              <span v-if="row.opponents.length" class="m-vs"> · 对手 {{ row.opponents.join(' / ') }}</span>
            </span>
            <span v-if="row.where" class="m-where">{{ row.where }}</span>
          </div>
        </div>
      </div>
    </van-popup>

    <!-- 分享预览遮罩：与报名海报共用（微信内的交互约束都在组件里） -->
    <ShareImageOverlay
      v-if="shareUrl"
      :url="shareUrl"
      :file="shareFile"
      :file-name="SHARE_CARD_FILE"
      :share-title="ranking?.tournament_title || '积分榜'"
      @close="closeShare"
    />
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import api from '@/api/client'
import { showFailToast, showLoadingToast } from 'vant'
import { renderRankingCard, SHARE_CARD_FILE, type ShareCardPlayer } from '@/utils/shareCard'
import ShareImageOverlay from '@/components/ShareImageOverlay.vue'
import { useWebSocket } from '@/composables/useWebSocket'
import { useResumeRefresh } from '@/composables/useResumeRefresh'
import { useGoBack } from '@/composables/useGoBack'
import { buildRankDetail } from '@/utils/rankDetail'
import AppHero from '@/components/AppHero.vue'

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

/* ---------------- 点胜/负数字看明细 ----------------
 * 数据来自 /tournaments/{id}/rounds（第一次点才拉，之后复用），在本地筛出
 * 「已结束 + 我在这场的某个 pairing 里 + 赢/输」的场次；筛选逻辑抽在
 * utils/rankDetail.ts（有单测），这里只管取数与弹层状态。
 * 为什么不在后端加接口：这些字段对阵表接口本来就有（全场最多 100 场），
 * 且「第N场」的编号规则要和对阵表完全一致（withGlobalIndex）。
 * 注意数字口径：8-4 = 胜-负，分母是**已结束**的场次，进行中的比赛不计入。
 */
const showMatches = ref(false)
const matchTarget = ref<any>(null)
const matchResult = ref<'win' | 'lose'>('win')
const rounds = ref<any[]>([])
const roundsLoaded = ref(false)
const matchesLoading = ref(false)
const matchesFailed = ref(false)

// 标题和列表**同源**（都从赛程算），不用积分榜行上的数字：
// 否则赛事进行中刷新一次排名，标题的"3 胜"就可能和列表里的 2 条对不上
const rankDetail = computed(() =>
  buildRankDetail(rounds.value, matchTarget.value?.user_id, matchResult.value),
)

const matchTitle = computed(() => {
  const t = matchTarget.value
  if (!t) return ''
  const n = rankDetail.value.rows.length
  return `${t.username} · ${n} ${matchResult.value === 'win' ? '胜' : '负'}（已结束 ${rankDetail.value.finished} 场）`
})

/** 有已打完的比赛才让数字可点（0 场的点开只会是空列表） */
function canOpen(p: any) {
  return (p?.matches_played || 0) > 0
}

async function loadRounds(force = false) {
  if (roundsLoaded.value && !force) return
  // 已经有数据时静默刷新（弹层开着时被广播触发），别闪一下 loading
  matchesLoading.value = !rounds.value.length
  matchesFailed.value = false
  try {
    const res = await api.get(`/tournaments/${tid}/rounds`, { skipLoading: true } as any)
    rounds.value = res.data || []
    roundsLoaded.value = true
  } catch {
    // 已经有旧数据时不要切到失败态：旧列表比"错误页"有用
    if (!rounds.value.length) matchesFailed.value = true
  } finally {
    matchesLoading.value = false
  }
}

async function openMatches(p: any, result: 'win' | 'lose') {
  if (!canOpen(p)) return
  matchTarget.value = p
  matchResult.value = result
  showMatches.value = true
  await loadRounds()
}

async function fetchRankings(skipLoading = false) {
  const res = await api.get(`/tournaments/${route.params.id}/rankings`, { skipLoading } as any)
  ranking.value = res.data
}

async function onRefresh() {
  try {
    await fetchRankings(true)
    roundsLoaded.value = false   // 赛程也作废，下次点数字重新拉
  } finally { refreshing.value = false }
}
// 后台静默刷新：实时广播触发，不弹全局「加载中...」
// 明细是赛程派生的：比赛有变动就作废缓存（弹层开着时顺手静默重拉，
// 否则用户会看到"标题说 3 胜、列表只有 2 条"这种前后不一致）
watch(lastMessage, (msg) => {
  if (msg?.type !== 'match_updated') return
  fetchRankings(true)
  roundsLoaded.value = false
  if (showMatches.value) loadRounds(true)
})
// 锁屏/后台返回时补一次刷新（冻结期间 WebSocket 可能已断，不再收得到广播）
useResumeRefresh(() => {
  fetchRankings(true)
  roundsLoaded.value = false
  if (showMatches.value) loadRounds(true)
})
onMounted(async () => {
  await Promise.all([auth.fetchMe(), fetchRankings()])
})

/* ---------------- 分享（生成图片） ----------------
 * 交互约束（微信内只能长按保存、iOS 微信里"分享到微信"必然失败等）都已经收进
 * components/ShareImageOverlay.vue，与报名海报共用；这里只负责出图。
 * 另注：要让"微信内一键分享成卡片"必须接公众号 JS-SDK（要认证公众号 + 后端签名），
 * 而且那分享出去的是链接卡片，不是这张图片本身。
 */
const shareUrl = ref('')
const shareFile = ref<File | null>(null)

function closeShare() {
  if (shareUrl.value) URL.revokeObjectURL(shareUrl.value)
  shareUrl.value = ''
  shareFile.value = null
}

async function openShare() {
  const list = visibleRankings.value
  if (!list.length) {
    showFailToast('暂无可分享的数据')
    return
  }
  const toast = showLoadingToast({ message: '正在生成图片…', forbidClick: true, duration: 0 })
  try {
    const players: ShareCardPlayer[] = list.map((p: any) => ({
      rank: p.rank,
      username: p.username,
      avatar: p.avatar,
      matches_played: p.matches_played,
      matches_won: p.matches_won,
      matches_lost: p.matches_lost,
      point_diff: p.point_diff,
      is_active: p.is_active,
      is_me: p.user_id === auth.user?.id,
    }))
    const card = await renderRankingCard({
      title: ranking.value?.tournament_title || '积分榜',
      players,
    })
    closeShare() // 释放上一张的 object URL
    shareUrl.value = card.url
    shareFile.value = new File([card.blob], SHARE_CARD_FILE, { type: 'image/png' })
  } catch {
    showFailToast('生成图片失败，请重试')
  } finally {
    toast.close()
  }
}

// 组件销毁时释放 object URL，否则长列表图片会一直占着内存
onUnmounted(closeShare)
</script>

<style scoped>
.rank-page { display: flex; flex-direction: column; background: #fff; }
.rank-scroll { flex: 1; overflow-y: auto; }
.pull-fill { min-height: 100%; }
.pull-inner { padding-bottom: var(--tabbar-reserve); }
.loading { display: flex; justify-content: center; margin-top: 80px; }

/* ---- 头图里的导航与标题 ---- */
.rank-nav { display: flex; align-items: center; justify-content: space-between; padding-top: 4px; }
.nav-back {
  background: none; border: 0; padding: 0; cursor: pointer;
  font-size: 13px; color: #fff; font-family: inherit;
}
.nav-title { font-size: 16px; font-weight: 700; letter-spacing: .3px; text-shadow: 0 1px 3px rgba(8, 44, 96, .26); }
.share-btn { color: #fff; padding: 2px; }
.rank-name { font-size: 21px; font-weight: 800; letter-spacing: -.4px; margin-top: 14px; }
.rank-kind { font-size: 11.5px; margin-top: 6px; }

/* ---- 白色浮层 ---- */
.rank-sheet {
  position: relative;
  margin-top: -28px; border-radius: 28px 28px 0 0; min-height: calc(100vh - 160px);
  background: #fff; padding: 16px 12px 12px;
  box-shadow: 0 -10px 24px rgba(9, 52, 110, .12);
  border-top: 1.5px solid rgba(255, 255, 255, .8);
}
.rank-card {
  background: #fff; border-radius: 16px; padding: 6px 6px 4px;
  border: 1px solid #eef3fa; box-shadow: 0 2px 8px rgba(17, 45, 90, .045);
}

/* 五列网格：排名 / 队员 / 比分 / 净胜 / 胜率（列宽固定，行与表头共用一套） */
.rank-head,
.rank-row {
  display: grid;
  grid-template-columns: 40px minmax(0, 1fr) 74px 54px 56px;
  align-items: center;
}
.rank-head { font-size: 11px; color: #98a1ad; padding: 6px 6px 8px; }
.rank-head span:nth-child(n + 3) { text-align: center; }

.rank-row {
  padding: 9px 6px; border-radius: 12px;
  position: relative;
}
.rank-row + .rank-row::before {
  content: ""; position: absolute; top: 0; left: 6px; right: 6px; height: 1px; background: #f1f5fb;
}
.rank-row.active { background: #eaf9f0; }
.rank-row.active::before, .rank-row.active + .rank-row::before { background: transparent; }
.rank-row.dropped { opacity: .5; }

.col-rank {
  width: 40px; font-weight: 800; font-size: 14px; color: #46505f; text-align: center;
  flex-shrink: 0; font-variant-numeric: tabular-nums;
}
.col-player { display: flex; align-items: center; gap: 7px; min-width: 0; }
.p-name {
  font-size: 12.5px; color: #23262b; font-weight: 600;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.col-wl { text-align: center; font-size: 13px; color: #666; font-variant-numeric: tabular-nums; }
.wl-win { font-style: normal; color: #0aa054; font-weight: 800; }
/* 负场保持中性色（不改原有观感），但两个数字都可以点 */
.wl-lose { font-style: normal; font-weight: 800; color: #7c8698; }
.wl-dash { margin: 0 1px; color: #c6cfdd; }
/* 可点的数字：**不加**静态下划线 —— 15px 的数字上压一条虚线下划线，看起来像多出来的
   短横（"2 -1" 会被看成 "2 --1"）。改用按下时的高亮做反馈，干净且不误导。 */
.col-wl .tappable { cursor: pointer; padding: 0 2px; border-radius: 5px; }
.col-wl .tappable:active { background: rgba(0, 0, 0, .07); }
.col-diff { text-align: center; font-size: 13px; font-weight: 800; font-variant-numeric: tabular-nums; }
.positive { color: #0aa054; }
.negative { color: #e8483f; }
.col-rate { text-align: center; font-weight: 800; font-size: 14px; color: #23262b; font-variant-numeric: tabular-nums; }

/* ---- 点胜/负看明细 ---- */
.picker-toolbar {
  display: flex; align-items: center; justify-content: space-between;
  padding: 12px 16px; font-size: 14px; color: #1989fa; flex-shrink: 0;
}
.picker-title { color: #323233; font-weight: 600; font-size: 15px; }
.sheet-loading { display: flex; justify-content: center; padding: 40px 0; }
.match-body { padding: 0 16px 16px; }
.match-empty { padding: 40px 0; text-align: center; color: #969799; font-size: 13px; }
.match-row {
  display: flex; align-items: baseline; gap: 8px;
  padding: 9px 0; border-bottom: 1px solid #f5f5f5; font-size: 13px;
}
.match-row:last-child { border-bottom: none; }
.m-no { color: #969799; font-size: 12px; flex-shrink: 0; }
.m-score { font-weight: 700; flex-shrink: 0; font-variant-numeric: tabular-nums; }
.m-score.won { color: #07c160; }
.m-score.lost { color: #e74c3c; }
/* 搭挡/对手一行里允许折行：名字不能截断（场地时间在右侧固定） */
.m-detail { flex: 1; min-width: 0; color: #323233; word-break: break-word; }
.m-vs { color: #969799; }
.m-where { color: #c8c9cc; font-size: 11px; flex-shrink: 0; }
</style>
