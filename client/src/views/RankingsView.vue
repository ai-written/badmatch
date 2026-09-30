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
          <span class="col-wl"><em class="wl-win">{{ p.matches_won }}</em>-{{ p.matches_lost }}</span>
          <span class="col-diff" :class="p.point_diff >= 0 ? 'positive' : 'negative'">{{ p.point_diff > 0 ? '+' : '' }}{{ p.point_diff }}</span>
          <span class="col-rate">{{ winRate(p) }}%</span>
        </div>
      </div>
    </template>
      </div>
    </van-pull-refresh>
    </div>

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

async function fetchRankings(skipLoading = false) {
  const res = await api.get(`/tournaments/${route.params.id}/rankings`, { skipLoading } as any)
  ranking.value = res.data
}

async function onRefresh() {
  try { await fetchRankings(true) } finally { refreshing.value = false }
}
// 后台静默刷新：实时广播触发，不弹全局「加载中...」
watch(lastMessage, (msg) => { if (msg?.type === 'match_updated') fetchRankings(true) })
// 锁屏/后台返回时补一次刷新（冻结期间 WebSocket 可能已断，不再收得到广播）
useResumeRefresh(() => fetchRankings(true))
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
.col-wl { flex: 1; text-align: center; font-size: 15px; color: #666; letter-spacing: 2px; }
.wl-win { font-style: normal; color: #e74c3c; font-weight: 600; margin-right: 2px; }
.col-diff { flex: 1; text-align: center; font-size: 15px; font-weight: 600; }
.positive { color: #07c160; }
.negative { color: #e74c3c; }
.col-rate { flex: 1; text-align: center; font-weight: 700; font-size: 17px; color: #333; }

/* ---- 分享 ---- */
.share-btn { color: #1989fa; padding: 4px 2px; }
</style>
