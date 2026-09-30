/**
 * 「积分榜」分享图生成（纯 canvas 手绘，不引入 html2canvas 之类的截图库）。
 *
 * 为什么不用 DOM 截图库：分享图要的是"一张干净的卡片"，而不是把页面原样拍下来。
 * 手绘可以自己决定排版、去掉滚动条/导航栏/下拉刷新这些界面残留，
 * 也不用担心截图库对 CSS 的支持差异，产物在所有设备上完全一致。
 *
 * 两个刻意的取舍：
 * 1) 只画同源头像（/static/... 或 data:）。跨域图片（页面上"没设头像"时会回退到
 *    外链默认头像）一旦 drawImage 就会把 canvas 标记为 tainted，随后 toBlob 直接抛
 *    SecurityError —— 也就是偏偏在最常见的"没头像"场景下整个分享功能不可用。
 *    这类情况统一退回「首字彩圈」，比随机猫图也更适合对外分享。
 * 2) 前三名不画 🥇🥈🥉：emoji 字形各设备不同，canvas 里的度量也不一致，同一张图
 *    在不同手机上会错位。改成自绘的金/银/铜圆牌，跨端完全一致。
 *
 * 量宽截断、圆角、图片加载、时间与域名格式化在 utils/canvasKit.ts，与报名海报共用。
 */
import {
  CANVAS_W as W, PAD, MAX_CANVAS_PIXELS,
  font, ellipsis, loadImage as loadAvatar,
  formatTime, displayHost,
} from './canvasKit'

export interface ShareCardPlayer {
  rank: number
  username: string
  avatar?: string | null
  matches_played: number
  matches_won: number
  matches_lost: number
  point_diff: number
  is_active: boolean
  /** 当前登录用户，会在图里高亮（与页面一致） */
  is_me?: boolean
}

export interface ShareCardOptions {
  title: string
  players: ShareCardPlayer[]
  generatedAt?: Date
}

export interface ShareCardResult {
  blob: Blob
  /** 预览用的 object URL，用完请 URL.revokeObjectURL */
  url: string
  width: number
  height: number
}

export const SHARE_CARD_FILE = '积分榜.png'

const SCALE = 2          // 导出倍率：实际 1500px 宽，够清晰、体积也可接受
const HEADER_H = 172
const TABLE_HEAD_H = 56
const ROW_H = 76
const FOOTER_H = 96

/* 列位置（与页面表格同一套列语义，便于两边对照） */
const COL_RANK = 66      // 名次中心
const COL_AVATAR = 134   // 头像中心
const COL_NAME = 170     // 昵称左边界
const COL_WL = 470       // 胜负中心
const COL_DIFF = 575     // 净胜分中心
const COL_RATE = W - PAD // 胜率右边界

const MEDAL = ['#f5c518', '#b9c2cc', '#d8a06a']          // 金 银 铜
const AVATAR_COLORS = ['#1989fa', '#07c160', '#ee0a24', '#7232dd', '#ff976a', '#00b8d4']

/** 圆形裁剪 + 居中裁切（避免非方图被拉变形） */
function drawRoundImage(ctx: CanvasRenderingContext2D, img: HTMLImageElement, cx: number, cy: number, r: number) {
  const size = Math.min(img.width, img.height)
  const sx = (img.width - size) / 2
  const sy = (img.height - size) / 2
  ctx.save()
  ctx.beginPath()
  ctx.arc(cx, cy, r, 0, Math.PI * 2)
  ctx.clip()
  ctx.drawImage(img, sx, sy, size, size, cx - r, cy - r, r * 2, r * 2)
  ctx.restore()
}

/** 没有可用头像时的首字彩圈：颜色由昵称哈希决定，同一个人每次颜色一致 */
function drawInitialCircle(ctx: CanvasRenderingContext2D, name: string, cx: number, cy: number, r: number) {
  const text = (name || '?').trim()
  let h = 0
  for (let i = 0; i < text.length; i++) h = (h * 31 + text.charCodeAt(i)) >>> 0
  ctx.save()
  ctx.beginPath()
  ctx.arc(cx, cy, r, 0, Math.PI * 2)
  ctx.fillStyle = AVATAR_COLORS[h % AVATAR_COLORS.length]
  ctx.fill()
  ctx.fillStyle = '#fff'
  ctx.font = font(Math.round(r * 0.92), 'bold')
  ctx.textAlign = 'center'
  ctx.textBaseline = 'middle'
  ctx.fillText(text.charAt(0) || '?', cx, cy + 1)
  ctx.restore()
}

export async function renderRankingCard(opt: ShareCardOptions): Promise<ShareCardResult> {
  const players = opt.players
  const height = HEADER_H + TABLE_HEAD_H + players.length * ROW_H + FOOTER_H

  const canvas = document.createElement('canvas')
  // 人数多时图会很高（赛事人数上限 64），而 canvas 的总像素有上限——iOS 尤其保守，
  // 超过约 16M 像素时 toBlob 会直接失败。宁可降到 1 倍图出一张略糊的，
  // 也不要让用户看到「生成图片失败」。
  const scale = W * height * SCALE * SCALE > MAX_CANVAS_PIXELS ? 1 : SCALE
  canvas.width = W * scale
  canvas.height = height * scale
  const ctx = canvas.getContext('2d')
  if (!ctx) throw new Error('当前浏览器不支持 canvas')
  ctx.scale(scale, scale)
  ctx.textBaseline = 'middle'

  /* ---- 头部：渐变底 + 品牌 + 赛事名 ---- */
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
  ctx.fillText('爱玩羽社 · 羽毛球循环赛', PAD, 50)

  ctx.fillStyle = '#ffffff'
  ctx.font = font(36, 'bold')
  ctx.fillText(ellipsis(ctx, opt.title || '积分榜', W - PAD * 2), PAD, 100)

  ctx.fillStyle = 'rgba(255,255,255,.88)'
  ctx.font = font(22)
  ctx.fillText(`积分榜 · 双打胜率 · 共 ${players.length} 人`, PAD, 142)

  /* ---- 表头 ---- */
  const headCy = HEADER_H + TABLE_HEAD_H / 2
  ctx.fillStyle = '#f7f8fa'
  ctx.fillRect(0, HEADER_H, W, TABLE_HEAD_H)
  ctx.fillStyle = '#969799'
  ctx.font = font(22)
  ctx.textAlign = 'center'
  ctx.fillText('名次', COL_RANK, headCy)
  ctx.textAlign = 'left'
  ctx.fillText('选手', COL_AVATAR - 23, headCy)
  ctx.textAlign = 'center'
  ctx.fillText('胜负', COL_WL, headCy)
  ctx.fillText('净胜分', COL_DIFF, headCy)
  ctx.textAlign = 'right'
  ctx.fillText('胜率', COL_RATE, headCy)

  /* ---- 头像并行预加载（不阻塞排版，失败就退回首字圈） ---- */
  const avatars = await Promise.all(players.map((p) => loadAvatar(p.avatar)))

  /* ---- 逐行 ---- */
  players.forEach((p, i) => {
    const top = HEADER_H + TABLE_HEAD_H + i * ROW_H
    const cy = top + ROW_H / 2

    if (p.is_me) {
      ctx.fillStyle = '#e8f5e9'
      ctx.fillRect(0, top, W, ROW_H)
    } else if (!p.is_active) {
      ctx.fillStyle = '#fafafa'
      ctx.fillRect(0, top, W, ROW_H)
    }
    ctx.strokeStyle = '#f2f3f5'
    ctx.lineWidth = 1
    ctx.beginPath()
    ctx.moveTo(PAD, top + ROW_H - 0.5)
    ctx.lineTo(W - PAD, top + ROW_H - 0.5)
    ctx.stroke()

    // 退赛选手整行淡出（与页面 .dropped 的 45% 一致），名次显示 '-'
    ctx.save()
    if (!p.is_active) ctx.globalAlpha = 0.45

    if (!p.is_active) {
      ctx.fillStyle = '#c8c9cc'
      ctx.font = font(26)
      ctx.textAlign = 'center'
      ctx.fillText('-', COL_RANK, cy)
    } else if (p.rank >= 1 && p.rank <= 3) {
      ctx.beginPath()
      ctx.arc(COL_RANK, cy, 19, 0, Math.PI * 2)
      ctx.fillStyle = MEDAL[p.rank - 1]
      ctx.fill()
      ctx.fillStyle = '#ffffff'
      ctx.font = font(24, 'bold')
      ctx.textAlign = 'center'
      ctx.fillText(String(p.rank), COL_RANK, cy + 1)
    } else {
      ctx.fillStyle = '#666666'
      ctx.font = font(26)
      ctx.textAlign = 'center'
      ctx.fillText(String(p.rank), COL_RANK, cy)
    }

    const img = avatars[i]
    if (img) drawRoundImage(ctx, img, COL_AVATAR, cy, 23)
    else drawInitialCircle(ctx, p.username, COL_AVATAR, cy, 23)

    ctx.textAlign = 'left'
    ctx.fillStyle = '#333333'
    ctx.font = font(25)
    // 昵称最多占到这里，给「胜负」列留出空间
    ctx.fillText(ellipsis(ctx, p.username, COL_WL - 46 - COL_NAME), COL_NAME, cy - 9)
    ctx.fillStyle = '#999999'
    ctx.font = font(20)
    ctx.fillText(`${p.matches_played} 场`, COL_NAME, cy + 17)

    const played = p.matches_played
    const rate = played === 0 ? 0 : Math.round((p.matches_won / played) * 100)
    ctx.textAlign = 'right'
    ctx.fillStyle = '#e74c3c'
    ctx.font = font(26, 'bold')
    ctx.fillText(String(p.matches_won), COL_WL - 6, cy)
    ctx.textAlign = 'left'
    ctx.fillStyle = '#666666'
    ctx.font = font(26)
    ctx.fillText(`-${p.matches_lost}`, COL_WL + 2, cy)

    ctx.textAlign = 'center'
    ctx.fillStyle = p.point_diff >= 0 ? '#07c160' : '#e74c3c'
    ctx.font = font(26, 'bold')
    ctx.fillText(`${p.point_diff > 0 ? '+' : ''}${p.point_diff}`, COL_DIFF, cy)

    ctx.fillStyle = '#333333'
    ctx.font = font(30, 'bold')
    ctx.textAlign = 'right'
    ctx.fillText(`${rate}%`, COL_RATE, cy)

    ctx.restore()
  })

  /* ---- 页脚 ---- */
  const footTop = height - FOOTER_H
  ctx.strokeStyle = '#f2f3f5'
  ctx.beginPath()
  ctx.moveTo(0, footTop + 0.5)
  ctx.lineTo(W, footTop + 0.5)
  ctx.stroke()
  ctx.fillStyle = '#969799'
  ctx.font = font(20)
  ctx.textAlign = 'left'
  ctx.fillText(`生成于 ${formatTime(opt.generatedAt ?? new Date())}`, PAD, footTop + FOOTER_H / 2)
  const host = displayHost()
  if (host) {
    ctx.textAlign = 'right'
    ctx.fillText(host, W - PAD, footTop + FOOTER_H / 2)
  }

  const blob = await new Promise<Blob>((resolve, reject) => {
    canvas.toBlob((b) => (b ? resolve(b) : reject(new Error('导出图片失败'))), 'image/png')
  })
  return { blob, url: URL.createObjectURL(blob), width: W, height }
}
