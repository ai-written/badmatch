/**
 * 分享类图片（积分榜分享图、报名海报）共用的 canvas 基础件。
 *
 * 两套排版完全不同，但底下的量宽截断、圆角矩形、同源图片加载、时间与域名
 * 格式化是一模一样的 —— 抽成一份，避免「改了一处忘了另一处」。
 * 这里也集中了几个踩过的坑：跨域图会污染 canvas、中文字体要按平台回退、
 * 局域网调试地址不该印到分享图上。
 */

export const CANVAS_W = 750              // 逻辑宽度
export const PAD = 36
// canvas 总像素上限（保守取 14M；iOS 上的实际限制约 16M）。人数多时 2 倍图会顶到
// 上限，超过就退成 1 倍图——宁可出一张略糊的，也不能让用户看到「生成图片失败」。
export const MAX_CANVAS_PIXELS = 14_000_000

const FONT = '-apple-system, BlinkMacSystemFont, "PingFang SC", "Microsoft YaHei", "Helvetica Neue", Arial, sans-serif'
export const font = (size: number, weight = 'normal') => `${weight} ${size}px ${FONT}`

export function roundRect(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, r: number) {
  ctx.beginPath()
  ctx.moveTo(x + r, y)
  ctx.lineTo(x + w - r, y)
  ctx.arcTo(x + w, y, x + w, y + r, r)
  ctx.lineTo(x + w, y + h - r)
  ctx.arcTo(x + w, y + h, x + w - r, y + h, r)
  ctx.lineTo(x + r, y + h)
  ctx.arcTo(x, y + h, x, y + h - r, r)
  ctx.lineTo(x, y + r)
  ctx.arcTo(x, y, x + r, y, r)
  ctx.closePath()
}

/** 超宽就截断加省略号（canvas 没有 ellipsis，只能自己量） */
export function ellipsis(ctx: CanvasRenderingContext2D, text: string, maxWidth: number) {
  if (ctx.measureText(text).width <= maxWidth) return text
  let t = text
  while (t.length > 1 && ctx.measureText(t + '…').width > maxWidth) t = t.slice(0, -1)
  return t + '…'
}

/**
 * 加载图片：只允许同源、data: 或 blob:，失败/超时返回 null（调用方自行降级）。
 *
 * 白名单是硬的，不是「尽量加载」：跨域图片一旦 drawImage 就会把 canvas 标记为
 * tainted，随后 toBlob 直接抛 SecurityError —— 页面上「没设头像」时会回退到
 * 外链默认头像，二维码来自接口（blob:），这两种都会踩到。
 */
export function loadImage(src?: string | null): Promise<HTMLImageElement | null> {
  return new Promise((resolve) => {
    if (!src) return resolve(null)
    let url: URL
    try {
      url = new URL(src, location.origin)
    } catch {
      return resolve(null)
    }
    if (url.protocol !== 'data:' && url.origin !== location.origin) return resolve(null)

    const img = new Image()
    let settled = false
    const finish = (v: HTMLImageElement | null) => {
      if (settled) return
      settled = true
      resolve(v)
    }
    // 图片加载不能拖住出图：1.5 秒没回来就当没有
    const timer = setTimeout(() => finish(null), 1500)
    img.onload = () => { clearTimeout(timer); finish(img) }
    img.onerror = () => { clearTimeout(timer); finish(null) }
    img.src = url.href
  })
}

export function pad2(n: number) {
  return n < 10 ? `0${n}` : String(n)
}

/** MM-DD HH:mm（分享图角落的生成时间） */
export function formatTime(d: Date) {
  return `${pad2(d.getMonth() + 1)}-${pad2(d.getDate())} ${pad2(d.getHours())}:${pad2(d.getMinutes())}`
}

/** 本地/局域网调试时 location.host 是 localhost 或 192.168.x.x，印在分享图上没意义
 *  （线上会是真实域名），这类地址直接不写。 */
export function displayHost() {
  const h = location.host
  const name = location.hostname
  if (!h || !name) return ''
  if (name === 'localhost' || name === '0.0.0.0' || name === '[::1]' || name === '::1') return ''
  if (/^127\./.test(name)) return ''
  if (/^10\./.test(name) || /^192\.168\./.test(name)) return ''
  if (/^172\.(1[6-9]|2\d|3[01])\./.test(name)) return ''   // 172.16.0.0 - 172.31.255.255
  return h
}
