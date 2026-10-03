/**
 * 对阵表「按人筛选」「隐藏较早的已完成比赛」与「进入页面自动定位」逻辑。
 *
 * 折叠：赛事进行中时只保留最后 1 场已完成的比赛，其余已完成的折叠起来，
 * 避免长赛事里打好几十场后要一直往下滚；赛事全部结束后不再折叠，完整展示赛果。
 *
 * 筛选：既能「只看某人」，也能「排除一个或多个人」（两者可叠加）。
 * 处理顺序很关键，见下方 withGlobalIndex / filterRounds / renderRounds 的注释。
 */

/**
 * 稳定的比赛编号：按赛程顺序编号，包含被折叠的比赛，保证「第N场」不会错位。
 *
 * 必须对**全量**赛程调用：先筛选再编号会让「第N场」随着筛选条件变化，
 * 同一个用户在不同筛选下看到同一场比赛却是不同编号。
 */
export function withGlobalIndex(rounds: any[]): any[] {
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
 * 按已编号的赛程渲染：丢掉被折叠的比赛，再丢掉已经空掉的轮次（避免留下空白间距）。
 *
 * 不重新编号 —— globalIdx 必须来自 withGlobalIndex(全量赛程)。
 */
export function renderRounds(rounds: any[], hiddenIds: Set<number>): any[] {
  return rounds
    .map((r) => ({ ...r, matches: (r.matches || []).filter((m: any) => !hiddenIds.has(m.id)) }))
    .filter((r) => r.matches.length > 0)
}

// ---------------------------------------------------------------- 按人筛选

/** 筛选条件：只看某一人（null=不限制）+ 排除若干人（可多选，与前者叠加） */
export interface ScheduleFilter {
  onlyPlayerId: number | null
  excludePlayerIds: number[]
}

/** 空条件。用函数而不是共享常量：常量里的 excludePlayerIds 数组会被所有调用方共用，
 *  任何一处就地修改都会污染其他人（工厂函数每次给一份新的）。 */
export function emptyFilter(): ScheduleFilter {
  return { onlyPlayerId: null, excludePlayerIds: [] }
}

export function isFilterActive(f: ScheduleFilter): boolean {
  return f.onlyPlayerId != null || f.excludePlayerIds.length > 0
}

/**
 * 把外部来源（localStorage / 用户改动后残留）的条件收敛成合法值：
 * - 类型不对的丢弃（存的是别人的旧版本数据时不能把页面搞崩）
 * - 「只看的人」同时被排除是自相矛盾的（结果必为空），这里以「只看」优先，
 *   从排除列表里去掉他
 */
export function normalizeFilter(input: any): ScheduleFilter {
  const only = typeof input?.onlyPlayerId === 'number' ? input.onlyPlayerId : null
  const raw: any[] = Array.isArray(input?.excludePlayerIds) ? input.excludePlayerIds : []
  const exclude = [...new Set(raw.filter((x) => typeof x === 'number'))].filter((id) => id !== only)
  return { onlyPlayerId: only, excludePlayerIds: exclude }
}

/** 一场比赛里的 4 名选手 */
export function matchPlayers(m: any): any[] {
  return [m?.pairing_a?.player_a, m?.pairing_a?.player_b, m?.pairing_b?.player_a, m?.pairing_b?.player_b]
    .filter(Boolean)
}

/** 赛程里出现过的所有选手（按首次出现顺序去重），供筛选面板列选项 */
export function playersInRounds(rounds: any[]): { id: number; username: string; avatar: string }[] {
  const seen = new Map<number, any>()
  for (const r of rounds) {
    for (const m of r.matches || []) {
      for (const p of matchPlayers(m)) {
        if (!seen.has(p.id)) seen.set(p.id, { id: p.id, username: p.username, avatar: p.avatar || '' })
      }
    }
  }
  return [...seen.values()]
}

/** 这场比赛是否该显示：「只看的人」在场 且 场上没有任何被排除的人 */
export function matchVisible(m: any, filter: ScheduleFilter): boolean {
  const ids = matchPlayers(m).map((p) => p.id)
  if (filter.onlyPlayerId != null && !ids.includes(filter.onlyPlayerId)) return false
  const excluded = new Set(filter.excludePlayerIds)
  return !ids.some((id) => excluded.has(id))
}

/**
 * 按人裁剪赛程（保留轮次结构，空轮次也先留着 —— 后续还要算折叠）。
 *
 * 必须传入**已编号**的赛程，裁剪时不动 globalIdx。
 */
export function filterRounds(rounds: any[], filter: ScheduleFilter): any[] {
  if (!isFilterActive(filter)) return rounds
  return rounds.map((r) => ({
    ...r,
    matches: (r.matches || []).filter((m: any) => matchVisible(m, filter)),
  }))
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
