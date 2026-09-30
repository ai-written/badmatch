/**
 * 「报名海报」生成（纯 canvas 手绘，基础件复用 utils/canvasKit.ts）。
 *
 * 为什么需要这张图：链接发进微信群时，卡片长什么样由微信自己抓，控制不了；而报名页
 * 是需要登录的 SPA，群里的人点进来先落到登录页 —— 吸引力远不如一张写清「时间 /
 * 地点 / 还剩几个名额」的图片。图片 + 二维码不受任何接口与资质限制，微信里长按即可
 * 保存转发，是"没有认证公众号"时最接近海报式分享的做法。
 *
 * 二维码由后端出图（server/app/api/poster.py）：前端不必为一张二维码引依赖，
 * 且码里的地址由服务端拼装（含邀请码），不会变成「任意链接生成器」。
 *
 * 与积分榜分享图一样：不用 emoji（各端字形与度量不一致会错位），不画跨域图片
 * （会污染 canvas 导致导出抛 SecurityError）。
 */
import {
  CANVAS_W, MAX_CANVAS_PIXELS,
  font, ellipsis, roundRect, loadImage, displayHost,
} from './canvasKit'

export interface PosterOptions {
  /** 赛事标题 */
  title: string
  /** 状态文案，如「报名中」「报名即将开放」「报名已满」 */
  statusText: string
  /** 时间文案，如「10月3日(周六) 19:00~22:00」 */
  timeText: string
  /** 地点文案，如「XX体育馆 3号场」 */
  placeText: string
  /** 第三个行的标签，默认「名额」；开赛后传「人数」 */
  quotaLabel?: string
  /** 名额文案，如「11/12 人（还剩 1 个）」；开赛后传「11 人参赛」 */
  quotaText: string
  /** 二维码旁的行动号召，默认「扫码报名」；开赛后传「扫码看赛程」 */
  qrTitle?: string
  /** 发起人昵称：取不到就整行不画（比如报名列表还没加载出来） */
  hostName?: string | null
  /** 二维码图片地址（blob:，来自后端接口） */
  qrSrc: string
}

export interface PosterResult {
  blob: Blob
  /** 预览用的 object URL，用完请 URL.revokeObjectURL */
  url: string
  width: number
  height: number
}

export const POSTER_FILE = '报名海报.png'

const W = CANVAS_W
const SCALE = 2
const PAD = 40
const HEADER_H = 200
const ROW_H = 78
const BODY_TOP = 28
const QR_CARD_H = 320
const QR_SIZE = 240
const FOOTER_H = 96

const LABEL_W = 92               // 「时间」这类标签占的宽度，值从它后面开始

export async function renderSignupPoster(opt: PosterOptions): Promise<PosterResult> {
  // 行内容先定下来：高度、二维码位置、行间细线都跟着它走。
  // 别写死「3 行」的常量——以后加一行（比如赛制）时容易忘了同步，页脚就被挤出画面。
  const rows: Array<[string, string]> = [
    ['时间', opt.timeText],
    ['地点', opt.placeText],
    [opt.quotaLabel || '名额', opt.quotaText],
  ]
  const height = HEADER_H + BODY_TOP + ROW_H * rows.length + BODY_TOP + QR_CARD_H + FOOTER_H

  const canvas = document.createElement('canvas')
  const scale = W * height * SCALE * SCALE > MAX_CANVAS_PIXELS ? 1 : SCALE
  canvas.width = W * scale
  canvas.height = height * scale
  const ctx = canvas.getContext('2d')
  if (!ctx) throw new Error('当前浏览器不支持 canvas')
  ctx.scale(scale, scale)
  ctx.textBaseline = 'middle'

  /* ---- 头部：渐变底 + 品牌 + 标题 + 状态 ---- */
  const grad = ctx.createLinearGradient(0, 0, W, HEADER_H)
  grad.addColorStop(0, '#1989fa')
  grad.addColorStop(1, '#0b6fd0')
  ctx.fillStyle = grad
  ctx.fillRect(0, 0, W, HEADER_H)
  ctx.fillStyle = '#ffffff'
  ctx.fillRect(0, HEADER_H, W, height - HEADER_H)

  ctx.textAlign = 'left'
  ctx.fillStyle = 'rgba(255,255,255,.78)'
  ctx.font = font(22)
  ctx.fillText('爱玩羽社 · 羽毛球局', PAD, 54)

  ctx.fillStyle = '#ffffff'
  ctx.font = font(38, 'bold')
  ctx.fillText(ellipsis(ctx, opt.title || '羽毛球局', W - PAD * 2), PAD, 112)

  ctx.fillStyle = 'rgba(255,255,255,.9)'
  ctx.font = font(24)
  ctx.fillText(ellipsis(ctx, opt.statusText, W - PAD * 2), PAD, 162)

  /* ---- 信息行：标签 + 值 ---- */
  rows.forEach(([label, value], i) => {
    const top = HEADER_H + BODY_TOP + i * ROW_H
    const cy = top + ROW_H / 2

    // 行之间画细线（最后一行不画，直接接二维码卡片）
    if (i < rows.length - 1) {
      ctx.strokeStyle = '#f2f3f5'
      ctx.lineWidth = 1
      ctx.beginPath()
      ctx.moveTo(PAD, top + ROW_H - 0.5)
      ctx.lineTo(W - PAD, top + ROW_H - 0.5)
      ctx.stroke()
    }

    ctx.textAlign = 'left'
    ctx.fillStyle = '#969799'
    ctx.font = font(24)
    ctx.fillText(label, PAD, cy)

    ctx.fillStyle = '#333333'
    ctx.font = font(26)
    ctx.fillText(ellipsis(ctx, value || '待定', W - PAD - (PAD + LABEL_W)), PAD + LABEL_W, cy)
  })

  /* ---- 二维码卡片 ---- */
  const qrTop = HEADER_H + BODY_TOP + ROW_H * rows.length + BODY_TOP
  ctx.fillStyle = '#f7f8fa'
  roundRect(ctx, PAD, qrTop, W - PAD * 2, QR_CARD_H, 20)
  ctx.fill()

  // 没有二维码的海报没有意义：加载失败直接抛，让上层提示「生成失败，请重试」
  const qr = await loadImage(opt.qrSrc)
  if (!qr) throw new Error('二维码加载失败')

  const qrX = PAD + 40
  const qrY = qrTop + 40
  // 二维码本身是背景色出图（白底黑块），这里再垫一层白底：卡片底色是 #f7f8fa，
  // 直接画会让静区变成浅灰，略微降低识别率
  ctx.fillStyle = '#ffffff'
  ctx.fillRect(qrX, qrY, QR_SIZE, QR_SIZE)
  ctx.drawImage(qr, qrX, qrY, QR_SIZE, QR_SIZE)

  const textX = qrX + QR_SIZE + 36
  ctx.textAlign = 'left'
  ctx.fillStyle = '#333333'
  ctx.font = font(30, 'bold')
  ctx.fillText(opt.qrTitle || '扫码报名', textX, qrTop + 116)

  ctx.fillStyle = '#969799'
  ctx.font = font(22)
  ctx.fillText('微信长按识别二维码', textX, qrTop + 166)
  ctx.fillText('或转发给群里的球友', textX, qrTop + 202)

  /* ---- 页脚：发起人 + 站点域名（局域网调试地址不写，见 displayHost） ---- */
  const footTop = height - FOOTER_H
  ctx.strokeStyle = '#f2f3f5'
  ctx.beginPath()
  ctx.moveTo(0, footTop + 0.5)
  ctx.lineTo(W, footTop + 0.5)
  ctx.stroke()

  const host = displayHost()
  ctx.fillStyle = '#969799'
  ctx.font = font(22)
  ctx.textAlign = 'left'
  if (opt.hostName) ctx.fillText(`发起人：${ellipsis(ctx, opt.hostName, 300)}`, PAD, footTop + FOOTER_H / 2)

  if (host) {
    ctx.textAlign = 'right'
    ctx.fillStyle = '#c8c9cc'
    ctx.font = font(20)
    ctx.fillText(host, W - PAD, footTop + FOOTER_H / 2)
  }

  const blob = await new Promise<Blob>((resolve, reject) => {
    canvas.toBlob((b) => (b ? resolve(b) : reject(new Error('导出图片失败'))), 'image/png')
  })
  return { blob, url: URL.createObjectURL(blob), width: W, height }
}
