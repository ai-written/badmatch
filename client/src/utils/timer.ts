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

const MAX_MATCH_SECONDS = 3 * 60 * 60

/**
 * 由服务端时间戳算出「拉取那一刻的已进行秒数」。
 * 无效或为负时返回 0；超过 3 小时上限时返回 0（视为异常数据，如忘记结束、隔天补录）。
 */
export function serverElapsedSeconds(
  startedAt: unknown,
  serverNow: unknown,
  maxSeconds: number = MAX_MATCH_SECONDS,
): number {
  const s = parseMs(startedAt)
  const n = parseMs(serverNow)
  if (!Number.isFinite(s) || !Number.isFinite(n)) return 0
  const secs = Math.floor((n - s) / 1000)
  if (secs < 0 || secs > maxSeconds) return 0
  return secs
}

/**
 * 计时器当前显示值（秒）。
 * - finished：显示服务端给出的最终耗时（缺失则显示 0）
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
