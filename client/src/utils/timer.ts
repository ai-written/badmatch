/**
 * 计分页计时器的时间换算（纯函数，便于单测）。
 *
 * 基准来自服务端的 matches.started_at，配合响应里的 now 得到「已进行秒数」。
 * 两者都是服务端本地时间，前端只取差值，因此设备时区与时钟偏差都不影响结果。
 */

/** 把服务端返回的 ISO 时间解析为毫秒；无效返回 NaN */
function parseMs(v: unknown): number {
  if (typeof v !== 'string' || !v) return NaN
  const t = Date.parse(v)
  return Number.isFinite(t) ? t : NaN
}

/**
 * 「进行中」比赛显示多少时长后改为文字提示。
 * 忘记结束比赛时（隔天、甚至十几天）不该继续堆一个越来越长的时间戳，
 * 而是明确提示异常，让用户去结束它。
 */
export const ONGOING_DISPLAY_LIMIT_SECONDS = 24 * 60 * 60

/**
 * 由服务端时间戳算出「拉取那一刻的已进行秒数」。
 *
 * 注意：这里【不设上限】。对正在进行的比赛，「已进行 10 天」是事实而非脏数据，
 * 归零会导致计时器从 0 重新走字（曾因此出现过 bug）。超长显示由 timerDisplay 处理。
 * 只有时间戳无效或倒挂（服务端时钟异常）时返回 0。
 */
export function serverElapsedSeconds(startedAt: unknown, serverNow: unknown): number {
  const s = parseMs(startedAt)
  const n = parseMs(serverNow)
  if (!Number.isFinite(s) || !Number.isFinite(n)) return 0
  const secs = Math.floor((n - s) / 1000)
  return secs < 0 ? 0 : secs
}

/**
 * 计时器当前显示值（秒）。
 * - finished：显示服务端给出的最终耗时（缺失则回退基准值）
 * - ongoing：基准 + 本地外推（拉取之后经过的秒数）
 * - 其他（pending）：0，前端不显示
 */
export function displayElapsedSeconds(
  status: string | undefined,
  durationSeconds: number | null | undefined,
  serverElapsed: number,
  anchorAtMs: number,
  nowMs: number,
): number {
  if (status === 'finished') return durationSeconds ?? serverElapsed
  if (status !== 'ongoing') return 0
  const delta = anchorAtMs ? Math.max(0, Math.floor((nowMs - anchorAtMs) / 1000)) : 0
  return serverElapsed + delta
}

/** 秒数格式化为 mm:ss 或 hh:mm:ss */
export function formatElapsed(sec: number): string {
  const total = Math.max(0, Math.floor(sec))
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = total % 60
  const p = (v: number) => v.toString().padStart(2, '0')
  return h > 0 ? `${p(h)}:${p(m)}:${p(s)}` : `${p(m)}:${p(s)}`
}

/**
 * 导航栏要显示的计时文案。
 * - 未开始：空（不显示，连括号也不出现）
 * - 进行中且未超过上限：mm:ss / hh:mm:ss
 * - 进行中但已超过上限（多半是忘记结束）：文字提示，不再堆数字
 * - 已结束：服务端给的实际耗时；缺失（异常数据）时为空
 */
export function timerDisplay(
  status: string | undefined,
  elapsedSeconds: number,
  durationSeconds: number | null | undefined,
): string {
  if (status === 'ongoing') {
    if (elapsedSeconds > ONGOING_DISPLAY_LIMIT_SECONDS) return '已超 24 小时'
    return formatElapsed(elapsedSeconds)
  }
  if (status === 'finished' && durationSeconds != null) return formatElapsed(durationSeconds)
  return ''
}
