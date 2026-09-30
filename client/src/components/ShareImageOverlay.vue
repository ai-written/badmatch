<template>
  <div class="share-mask" @click.self="close">
    <div class="share-box">
      <img :src="url" class="share-img" :alt="fileName" />
      <p class="share-tip">{{ tip }}</p>
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
        <van-button plain round block @click="close">关闭</van-button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * 分享图预览遮罩（积分榜分享图、报名海报共用）。
 *
 * 交互是被微信的现实约束逼出来的，别随手改：
 * - 网页无法把图片直接"发"进微信聊天。能调起系统分享面板的浏览器（Chrome/Android、
 *   iOS Safari）里可以选微信，走的是 navigator.share 的 files 能力；
 * - 微信内置浏览器（安卓 X5）没有 Web Share API，只能长按图片保存到相册再回聊天窗口发；
 * - iOS 微信是个例外：WKWebView 实现了 Web Share API，canShare({files}) 返回 true，
 *   但把文件交给「微信」这个分享目标时会被直接取消——面板闪一下就没了，而且抛的是
 *   AbortError（与用户主动取消无法区分）。所以微信内不能把"分享到微信"当主路径，
 *   判断顺序必须 isWeChat 在前（曾经写在 canShareFile 之后，导致微信里提示
 *   "可直接选微信发送"，把人引到必然失败的路径上）。
 * - Web Share API 只在「安全上下文」里存在：https 或 localhost 才有，
 *   用 http://192.168.x.x 这种局域网地址测试时，连系统浏览器都不会有分享按钮。
 */
import { computed } from 'vue'
import { showFailToast, showToast } from 'vant'

const props = withDefaults(defineProps<{
  /** 图片的 object URL（由调用方创建并负责 revoke） */
  url: string
  /** 用于系统分享的文件；不支持分享文件的环境传 null 也能正常降级 */
  file: File | null
  /** 保存时的文件名，如「积分榜.png」 */
  fileName?: string
  /** 系统分享面板里的标题 */
  shareTitle?: string
  /** 覆盖默认提示语（有明确用途的图，如报名海报，可以给更贴切的引导） */
  tip?: string
}>(), {
  fileName: '分享图.png',
  shareTitle: '',
})

const emit = defineEmits<{ close: [] }>()

const isWeChat = /MicroMessenger/i.test(navigator.userAgent)

// 有的浏览器有 navigator.share 但不支持分享文件，必须拿真实 File 试探 canShare
const canShareFile = computed(() =>
  !!props.file && typeof navigator.canShare === 'function' && navigator.canShare({ files: [props.file] })
)

// 微信内只承诺"其他应用"：发给微信自己会失败
const shareButtonText = computed(() =>
  isWeChat ? '分享到其他应用（QQ/邮件等）' : '分享到微信 / 其他应用'
)

const tip = computed(() => {
  if (props.tip) return props.tip
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

function close() {
  emit('close')
}

async function shareToApp() {
  if (!props.file || typeof navigator.share !== 'function') return
  try {
    await navigator.share({ files: [props.file], title: props.shareTitle || props.fileName })
  } catch (e: any) {
    if (e?.name !== 'AbortError') showFailToast('分享失败，可长按图片保存')
  }
}

function downloadImage() {
  const a = document.createElement('a')
  a.href = props.url
  a.download = props.fileName
  document.body.appendChild(a)
  a.click()
  a.remove()
  // 微信内置浏览器对 <a download> 的支持不一致（iOS 上可能毫无反应），
  // 所以给一句兜底提示——「点了没反应」比「提示你去长按」糟糕得多
  showToast(isWeChat ? '已发起保存；若相册里没有，请长按图片选择「存储图像」' : '已保存图片')
}
</script>

<style scoped>
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
