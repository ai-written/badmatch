<template>
  <div class="rank-page vh-page">
    <van-nav-bar title="积分榜" left-text="返回" left-arrow @click-left="goBack">
      <template #right>
        <van-icon name="share-o" size="20" class="share-btn" @click="openShare" />
      </template>
    </van-nav-bar>

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
            <van-image lazy-load round width="28" height="28" :src="p.avatar || defaultAvatar" />
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
.rank-page { display: flex; flex-direction: column; background: #f0f2f5; }
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
.col-wl { flex: 1; text-align: center; font-size: 15px; color: #666; }
.wl-win { font-style: normal; color: #e74c3c; font-weight: 600; }
/* 负场保持中性色（不改原有观感），但两个数字都可以点 */
.wl-lose { font-style: normal; font-weight: 600; color: #666; }
.wl-dash { margin: 0 1px; }
/* 可点的数字：**不加**静态下划线 —— 15px 的数字上压一条虚线下划线，看起来像多出来的
   短横（"2 -1" 会被看成 "2 --1"）。改用按下时的高亮做反馈，干净且不误导。 */
.col-wl .tappable { cursor: pointer; padding: 0 2px; border-radius: 4px; }
.col-wl .tappable:active { background: rgba(0, 0, 0, .07); }
.col-diff { flex: 1; text-align: center; font-size: 15px; font-weight: 600; }
.positive { color: #07c160; }
.negative { color: #e74c3c; }
.col-rate { flex: 1; text-align: center; font-weight: 700; font-size: 17px; color: #333; }

/* ---- 分享 ---- */
.share-btn { color: #1989fa; padding: 4px 2px; }

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
