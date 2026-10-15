<template>
  <div class="share-mask" @click.self="close">
    <div class="share-box">
      <img :src="url" class="share-img" :alt="fileName" />
      <p class="share-tip">{{ tip }}</p>
      <!-- 与分享机制无关的额外提醒（如「你还没有邀请码」）：单独一行，
           免得调用方为了加一句话就把 iOS/微信那套保存指引顶掉 -->
      <p v-if="notice" class="share-notice">{{ notice }}</p>
      <div class="share-actions">
        <!-- 会渲染哪些按钮由 utils/shareEnv.ts 的 visibleActions 决定（有单测）：
             - iOS：两个老入口都到不了相册——<a download> 只会把文件存进「文件」App，
               系统分享面板里选「微信」则对方收到的是「文件」气泡。所以只留一个入口：
               调系统面板选「存储图像」；连系统分享都没有时改成教长按存相册。
               iOS 微信里点它还可能被微信自己取消（面板闪一下就没了），故另补一句提示。
             - 非 iOS 的微信内（安卓 X5 没有 Web Share API）：主按钮给「保存图片」，
               分享按钮降级保留，因为发给 QQ/邮件等是正常的。
             - 其他环境：能用系统分享就主推分享，否则主推保存。 -->
        <van-button v-if="actions.includes('iosSave')" type="primary" round block @click="saveOnIOS">
          {{ iosSaveText }}
        </van-button>
        <van-button v-if="actions.includes('save')" type="primary" round block @click="downloadImage">保存图片</van-button>
        <van-button v-if="actions.includes('share')" :type="isWeChat ? 'default' : 'primary'" :plain="isWeChat" round block @click="shareToApp">{{ shareButtonText }}</van-button>
        <van-button v-if="actions.includes('savePlain')" plain round block @click="downloadImage">保存图片</van-button>
        <van-button plain round block @click="close">关闭</van-button>
      </div>
      <!-- 操作结果必须画在这个遮罩里：Vant 的 toast/dialog 默认 z-index 是 2000 起，
           而遮罩是 3000，弹出来的东西会藏在遮罩下面（看得见摸不着） -->
      <p v-if="hint" class="share-hint">{{ hint }}</p>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * 分享图预览遮罩（积分榜分享图、报名海报共用）。
 *
 * 交互是被微信/苹果的现实约束逼出来的，别随手改：
 * - 网页无法把图片直接"发"进微信聊天。能调起系统分享面板的浏览器（Chrome/Android、
 *   iOS Safari）里可以选微信，走的是 navigator.share 的 files 能力；
 * - 微信内置浏览器（安卓 X5）没有 Web Share API，只能长按图片保存到相册再回聊天窗口发；
 * - iOS 微信是个例外：WKWebView 实现了 Web Share API，canShare({files}) 返回 true，
 *   但把文件交给「微信」这个分享目标时会被直接取消——面板闪一下就没了，而且抛的是
 *   AbortError（与用户主动取消无法区分）。所以微信内不能把"分享到微信"当主路径，
 *   判断顺序必须 isWeChat 在前（曾经写在 canShareFile 之后，导致微信里提示
 *   "可直接选微信发送"，把人引到必然失败的路径上）。
 * - **iOS 上这两个入口都到不了相册**（实测踩到）：`<a download>` 把文件丢进「文件」App；
 *   系统分享面板里选「微信」发出去是一条 **文件气泡**（报名海报.png，带文档图标），
 *   对方还得自己"保存图片"一次才变成图片。根因在 **iOS 微信的分享扩展**：它只把相册里的
 *   照片当图片收，网页分享过去的文件一律按"文件"处理，网页侧改 mime/文件名都改不了。
 *   能进相册的只有系统动作：分享面板里的「存储图像」、或长按图片存到相册
 *   （微信自家菜单里那一项叫法各版本不同，所以提示语不写死动作名，
 *   文案表在 utils/shareEnv.ts，有单测）。
 *   所以 iOS 上不再提供「保存图片 / 分享到微信」，只留「存到相册 / 分享」+ 说清怎么点，
 *   详见 README「苹果手机（iOS）」一节。
 *   **安卓不受影响**：系统分享把 image/png 作为 ACTION_SEND 交给微信，收到的是图片
 *   （用户实测正常），所以非 iOS 分支一个字都不改。
 * - Web Share API 只在「安全上下文」里存在：https 或 localhost 才有，
 *   用 http://192.168.x.x 这种局域网地址测试时，连系统浏览器都不会有分享按钮。
 */
import { computed, ref } from 'vue'
import {
  detectIsIOS, isWeChatUA, shareTip, saveButtonLabel, shareButtonLabel, visibleActions,
  type ShareEnv,
} from '@/utils/shareEnv'

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
  /** 额外提醒（与分享机制无关，如「你还没有邀请码」），显示在提示语下面 */
  notice?: string
}>(), {
  fileName: '分享图.png',
  shareTitle: '',
})

const emit = defineEmits<{ close: [] }>()

const isWeChat = isWeChatUA(navigator.userAgent)
// iPhone / iPad：iPadOS 13+ 的 UA 是桌面版（MacIntel），要靠触摸点数认出来
const isIOS = detectIsIOS(navigator.userAgent, navigator.platform, navigator.maxTouchPoints)

// 有的浏览器有 navigator.share 但不支持分享文件，必须拿真实 File 试探 canShare
const canShareFile = computed(() =>
  !!props.file && typeof navigator.canShare === 'function' && navigator.canShare({ files: [props.file] })
)

const env = computed<ShareEnv>(() => ({
  isIOS,
  isWeChat,
  canShareFile: canShareFile.value,
  isSecureContext: window.isSecureContext,
}))

// 文案与按钮表都在 utils/shareEnv.ts（纯函数，可单测；iOS 分支没法在开发机上真机验证）
const actions = computed(() => visibleActions(env.value))
const shareButtonText = computed(() => shareButtonLabel(env.value))
const iosSaveText = computed(() => saveButtonLabel(env.value))
const tip = computed(() => props.tip || shareTip(env.value))

/** 操作结果提示（替代 toast：见 template 里的 z-index 说明） */
const hint = ref('')

function close() {
  emit('close')
}

async function shareToApp() {
  hint.value = ''
  if (!props.file || typeof navigator.share !== 'function') return
  try {
    await navigator.share({ files: [props.file], title: props.shareTitle || props.fileName })
  } catch (e: any) {
    if (e?.name === 'AbortError') {
      // iOS 微信里"面板闪一下就没了"抛的也是 AbortError（与用户主动取消分不开），
      // 那种情况下不发一句就等于点了没反应，所以微信内补一句注意（措辞带"若"，
      // 主动取消的人也不会被误导）
      if (isIOS && isWeChat) hint.value = '若分享面板一闪而过（微信没接住），请长按图片 →「存储图像」再发'
      return
    }
    hint.value = '分享失败，可长按图片保存后再发送'
  }
}

/**
 * iOS 的「存到相册」：只有系统面板里的「存储图像」才能进相册
 * （面板里也能选微信，但那等于"发文件"，见组件顶部注释）。
 * 没有系统分享能力时（http 局域网访问）给一段文字说明，别让按钮点了没反应。
 */
function saveOnIOS() {
  if (!canShareFile.value) {
    hint.value = '长按上方图片 → 存到相册，再回微信里以图片发送（直接分享给微信收到的是「文件」，对方还得自己再保存一次）'
    return
  }
  shareToApp()
}

function downloadImage() {
  const a = document.createElement('a')
  a.href = props.url
  a.download = props.fileName
  document.body.appendChild(a)
  a.click()
  a.remove()
  // 只有非 iOS 会走到这里：iOS 上 <a download> 只会把文件丢进「文件」App（进不了相册），
  // 那条路已被 saveOnIOS 取代。
  // 微信内置浏览器对 <a download> 的支持不一致（安卓 X5 上可能毫无反应），
  // 所以给一句兜底提示——「点了没反应」比「提示你去长按」糟糕得多
  hint.value = isWeChat ? '已发起保存；若相册里没有，请长按图片选择「存储图像」' : '已保存图片'
}
</script>

<style scoped>
.share-mask {
  position: fixed; top: 0; left: 0; right: 0; bottom: 0; z-index: 3000;
  display: flex; flex-direction: column; align-items: center;
  padding: 20px;
  /* 底部按钮要避开浏览器工具栏（--browser-gap 见 main.css，正常内核里是 0） */
  padding-bottom: calc(20px + var(--browser-gap, 0px));
  /* 小屏（SE 一类）上图片 + 提示语 + 按钮 + 结果提示会超过一屏：得能滚，
     否则底部的「关闭」被裁掉就出不来了。居中改用 .share-box 的 margin:auto，
     不用 justify-content:center —— 后者在内容溢出时会把顶部裁掉且滚不到 */
  overflow-y: auto;
  /* 滚到尽头不要把手指的滚动传给遮罩底下的页面 */
  overscroll-behavior: contain;
  background: rgba(0, 0, 0, .72);
}
.share-box { margin: auto; width: 100%; max-width: 340px; display: flex; flex-direction: column; align-items: center; }
.share-img {
  max-width: 100%;
  max-height: 56vh;
  max-height: calc(var(--vh, 1vh) * 56);
  border-radius: 10px; background: #fff; box-shadow: 0 8px 28px rgba(0, 0, 0, .45);
}
.share-tip { margin: 12px 0; font-size: 13px; line-height: 1.5; color: #e8e8e8; text-align: center; }
.share-notice { margin: -4px 0 12px; font-size: 12px; line-height: 1.5; color: #ffd591; text-align: center; }
.share-hint { margin: 12px 0 0; font-size: 13px; line-height: 1.5; color: #ffd591; text-align: center; }
.share-actions { width: 100%; display: flex; flex-direction: column; gap: 10px; }
</style>
