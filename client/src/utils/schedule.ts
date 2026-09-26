/**
 * 对阵表「隐藏较早的已完成比赛」与「进入页面自动定位」逻辑。
 *
 * 折叠：赛事进行中时只保留最后 1 场已完成的比赛，其余已完成的折叠起来，
 * 避免长赛事里打好几十场后要一直往下滚；赛事全部结束后不再折叠，完整展示赛果。
 */

/** 稳定的比赛编号：按赛程顺序编号，包含被折叠的比赛，保证「第N场」不会错位 */
function withGlobalIndex(rounds: any[]): any[] {
  let idx = 0
  return rounds.map((r) => ({
    ...r,
    matches: (r.matches || []).map((m: any) => ({ ...m, globalIdx: ++idx })),
  }))
}

/**
 * 计算需要折叠的已完成比赛 id。
 * - expanded 为 true 时返回空集（用户已展开）
 * - tournamentEnded 为 true 时返回空集：赛事已结束（含被提前结束）就不再折叠，
 *   让用户能完整回顾赛果。必须用赛事状态而不是「有无未打完的比赛」——
 *   手动提前结束时会留下 pending 比赛，两套判断会自相矛盾
 * - 没有「进行中 / 待打」的比赛时返回空集（所有比赛都打完了）
 * - 已完成的比赛 ≤ 1 场时返回空集（没有可隐藏的东西）
 * - 否则取最后 1 场已完成比赛保留，其余已完成比赛全部折叠
 *
 * 「最后」按赛程顺序而非结束时间：并场进行时结束时间会乱序。
 */
export function hiddenFinishedIds(
  rounds: any[],
  expanded: boolean,
  tournamentEnded = false,
): Set<number> {
  if (expanded || tournamentEnded) return new Set<number>()
  const all = rounds.flatMap((r) => r.matches || [])
  // 还有比赛没打完就保持折叠；全部结束则全部展开
  const hasUnfinished = all.some((m: any) => m.status === 'ongoing' || m.status === 'pending')
  if (!hasUnfinished) return new Set<number>()
  const finished = all.filter((m: any) => m.status === 'finished')
  if (finished.length <= 1) return new Set<number>()
  const keepId = finished[finished.length - 1].id
  return new Set<number>(finished.filter((m: any) => m.id !== keepId).map((m: any) => m.id))
}

/**
 * 生成用于渲染的轮次列表：先按全部比赛编号，再过滤掉被折叠的比赛，
 * 最后丢弃已经空掉的轮次（避免留下空白间距）。
 */
export function visibleRounds(rounds: any[], hiddenIds: Set<number>): any[] {
  return withGlobalIndex(rounds)
    .map((r) => ({ ...r, matches: r.matches.filter((m: any) => !hiddenIds.has(m.id)) }))
    .filter((r) => r.matches.length > 0)
}

/**
 * 进入对阵表时应该定位到哪一场：
 * 1. 优先第一个「进行中」的比赛
 * 2. 没有进行中的，取第一个「待打」的比赛
 * 3. 其余情况（全部已结束 / 无比赛）返回 null，不做滚动
 */
export function focusMatchId(rounds: any[]): number | null {
  const all = rounds.flatMap((r) => r.matches || [])
  const ongoing = all.find((m: any) => m.status === 'ongoing')
  if (ongoing) return ongoing.id
  const pending = all.find((m: any) => m.status === 'pending')
  return pending ? pending.id : null
}

/**
 * 计算自动定位后的列表滚动位置：让目标比赛落在可视区偏上约 1/3 处。
 *
 * 上方保留约 1/3 屏的上文（能看到前面打到哪），下方留出约 2/3 屏展示后续比赛，
 * 比纯居中更实用。抽成纯函数以便验证落点算术。
 *
 * @param offsetTop 目标相对滚动容器内容顶部的偏移（el.offsetTop，需容器为 offsetParent）
 * @param clientHeight 容器可视高度
 * @param upperRatio 目标上方保留的比例，默认 1/3
 */
export function focusScrollTop(offsetTop: number, clientHeight: number, upperRatio = 1 / 3): number {
  return Math.max(0, offsetTop - clientHeight * upperRatio)
}
