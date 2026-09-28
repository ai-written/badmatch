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

    <!-- 分享预览：自己写遮罩层，不用 van-popup —— 底部按钮同样要避开浏览器工具栏，
         留白里加上 --browser-gap（见 main.css）。 -->
    <div v-if="shareUrl" class="share-mask" @click.self="closeShare">
      <div class="share-box">
        <img :src="shareUrl" class="share-img" alt="积分榜分享图" />
        <p class="share-tip">{{ shareTip }}</p>
        <div class="share-actions">
          <!-- 按钮主次按环境排：
               - 微信内：主按钮给「保存图片」。iOS 微信虽然实现了 Web Share API（canShare
                 返回 true），但把文件交给微信自己的分享扩展时会被直接取消（面板闪一下就
                 消失，且抛的是 AbortError，前端无法区分），所以微信里不能把「分享到微信」
                 当主路径；分享按钮降级保留，因为发给 QQ/邮件等是正常的。
               - 其他环境：能用系统分享就主推分享，否则主推保存。 -->
          <van-button v-if="isWeChat || !canShareFile" type="primary" round block @click="downloadImage">保存图片</van-button>
          <van-button v-if="canShareFile" :type="isWeChat ? 'default' : 'primary'" :plain="isWeChat" round block @click="shareToApp">{{ shareButtonText }}</van-button>
          <van-button v-if="canShareFile && !isWeChat" plain round block @click="downloadImage">保存图片</van-button>
          <van-button plain round block @click="closeShare">关闭</van-button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import api from '@/api/client'
import { showFailToast, showLoadingToast, showToast } from 'vant'
import { renderRankingCard, SHARE_CARD_FILE, type ShareCardPlayer } from '@/utils/shareCard'
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
 * 微信的现实约束（决定了这里的交互）：
 * - 网页无法直接把图片"发"进微信聊天。能调起系统分享面板的浏览器（Chrome/Android、
 *   iOS Safari）里可以选微信，走的是 navigator.share 的 files 能力；
 * - 微信内置浏览器（安卓 X5）没有 Web Share API，只能长按图片保存到相册再回聊天窗口发；
 * - iOS 微信是另一回事：WKWebView 实现了 Web Share API，canShare({files}) 返回 true，
 *   但把文件交给「微信」这个分享目标时会被直接取消——面板闪一下就没了，而且抛的是
 *   AbortError（与用户主动取消无法区分）。所以微信内不能把"分享到微信"当主路径，
 *   判断顺序必须 isWeChat 在前（曾经写在 canShareFile 之后，导致微信里提示
 *   "可直接选微信发送"，把人引到必然失败的路径上）。
 * - 还要注意 Web Share API 只在「安全上下文」里存在：https 或 localhost 才有，
 *   用 http://192.168.x.x 这种局域网地址测试时，连系统浏览器都不会有分享按钮；
 * - 要让"微信内一键分享成卡片"必须接公众号 JS-SDK（需要 appId/secret + 后端签名），
 *   而且那分享出去的是链接卡片，不是这张图片本身。
 */
const isWeChat = /MicroMessenger/i.test(navigator.userAgent)
const shareUrl = ref('')
const shareFile = ref<File | null>(null)
const canShareFile = ref(false)

// 微信内只承诺"其他应用"：发给微信自己会失败
const shareButtonText = computed(() =>
  isWeChat ? '分享到其他应用（QQ/邮件等）' : '分享到微信 / 其他应用'
)

const shareTip = computed(() => {
  // 先判微信：iOS 微信里 canShare 是 true，但选「微信」必然失败
  if (isWeChat) {
    return canShareFile.value
      ? '微信内不能一键分享给好友：长按图片 → 存储图像，再回聊天窗口发送；发给其他应用可用下方按钮'
      : '微信内不能一键分享：长按图片 → 存储图像，再回聊天窗口发送'
  }
  if (canShareFile.value) return '点下方按钮可直接选微信发送；也可以长按图片保存后再发'
  // 按钮消失得给个理由，否则用户只会觉得"功能坏了"
  if (!window.isSecureContext) return 'http 访问下浏览器不开放系统分享（线上 https 才有分享按钮）：长按图片保存后再发送'
  return '长按图片保存到相册，或点「保存图片」'
})

function closeShare() {
  if (shareUrl.value) URL.revokeObjectURL(shareUrl.value)
  shareUrl.value = ''
  shareFile.value = null
  canShareFile.value = false
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
    const file = new File([card.blob], SHARE_CARD_FILE, { type: 'image/png' })
    shareFile.value = file
    // 有的浏览器有 navigator.share 但不支持分享文件，必须拿真实 File 试探 canShare
    canShareFile.value = typeof navigator.canShare === 'function' && navigator.canShare({ files: [file] })
  } catch {
    showFailToast('生成图片失败，请重试')
  } finally {
    toast.close()
  }
}

async function shareToApp() {
  const file = shareFile.value
  if (!file || typeof navigator.share !== 'function') return
  try {
    await navigator.share({ files: [file], title: ranking.value?.tournament_title || '积分榜' })
  } catch (e: any) {
    if (e?.name !== 'AbortError') showFailToast('分享失败，可长按图片保存')
  }
}

function downloadImage() {
  if (!shareUrl.value) return
  const a = document.createElement('a')
  a.href = shareUrl.value
  a.download = SHARE_CARD_FILE
  document.body.appendChild(a)
  a.click()
  a.remove()
  // 微信内置浏览器对 <a download> 的支持不一致（iOS 上可能毫无反应），
  // 所以给一句兜底提示——「点了没反应」比「提示你去长按」糟糕得多
  showToast(isWeChat ? '已发起保存；若相册里没有，请长按图片选择「存储图像」' : '已保存图片')
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
.share-mask {
  position: fixed; top: 0; left: 0; right: 0; bottom: 0; z-index: 3000;
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  padding: 20px;
  /* 底部按钮要避开浏览器工具栏（--browser-gap 见 main.css，正常内核里是 0） */
  padding-bottom: calc(20px + var(--browser-gap, 0px));
  background: rgba(0, 0, 0, .72);
}
.share-box { width: 100%; max-width: 340px; display: flex; flex-direction: column; align-items: center; }
.share-img {
  max-width: 100%;
  max-height: 56vh;
  max-height: calc(var(--vh, 1vh) * 56);
  border-radius: 10px; background: #fff; box-shadow: 0 8px 28px rgba(0, 0, 0, .45);
}
.share-tip { margin: 12px 0; font-size: 13px; line-height: 1.5; color: #e8e8e8; text-align: center; }
.share-actions { width: 100%; display: flex; flex-direction: column; gap: 10px; }
</style>
