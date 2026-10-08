/**
 * 对阵表「按人筛选」「隐藏较早的已完成比赛」与「进入页面自动定位」逻辑。
 *
 * 折叠：赛事进行中时只保留最后 1 场已完成的比赛，其余已完成的折叠起来，
 * 避免长赛事里打好几十场后要一直往下滚；赛事全部结束后不再折叠，完整展示赛果。
 *
 * 筛选：既能「只看某些人」（可多选），也能「排除一个或多个人」（可多选），两者可叠加。
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

/** 筛选条件：只看某些人（空数组=不限制，可多选）+ 排除若干人（可多选），两者叠加 */
export interface ScheduleFilter {
  onlyPlayerIds: number[]
  excludePlayerIds: number[]
}

/** 空条件。用函数而不是共享常量：常量里的数组会被所有调用方共用，
 *  任何一处就地修改都会污染其他人（工厂函数每次给一份新的）。 */
export function emptyFilter(): ScheduleFilter {
  return { onlyPlayerIds: [], excludePlayerIds: [] }
}

export function isFilterActive(f: ScheduleFilter): boolean {
  return f.onlyPlayerIds.length > 0 || f.excludePlayerIds.length > 0
}

/**
 * 合法的选手 id：必须是整数。
 * 只写 typeof x === 'number' 会放过 NaN / 浮点（脏数据或将来别处传错时），
 * 而它们会让 isFilterActive 为真却匹配不到任何人 —— 表现为「筛选打开后一场都没有」
 * 且摘要显示 `只看 #NaN`，很难排查。
 */
function isPlayerId(x: any): x is number {
  return Number.isInteger(x)
}

/**
 * 把外部来源（localStorage / 用户改动后残留）的条件收敛成合法值：
 * - 类型不对的丢弃（存的是别人的旧版本数据时不能把页面搞崩）
 * - 兼容升级前存的单选格式（onlyPlayerId: number）：老用户手机里那份条件照旧生效，
 *   不必手动清缓存；新格式为 onlyPlayerIds 数组
 * - 两个字段同时存在（半迁移/脏数据）时：新字段**有内容**就以它为准，为空则回落到旧值。
 *   直接「是数组就优先」会让 `{onlyPlayerIds: [], onlyPlayerId: 5}` 静默丢掉用户条件
 * - 同一人既「只看」又「排除」是自相矛盾的（结果必为空），这里以「只看」优先，
 *   把他从排除列表里去掉
 */
export function normalizeFilter(input: any): ScheduleFilter {
  const rawOnly: any[] = Array.isArray(input?.onlyPlayerIds) && input.onlyPlayerIds.length > 0
    ? input.onlyPlayerIds
    : typeof input?.onlyPlayerId === 'number' ? [input.onlyPlayerId] : []
  const only = [...new Set(rawOnly.filter(isPlayerId))]
  const raw: any[] = Array.isArray(input?.excludePlayerIds) ? input.excludePlayerIds : []
  const exclude = [...new Set(raw.filter(isPlayerId))].filter((id) => !only.includes(id))
  return { onlyPlayerIds: only, excludePlayerIds: exclude }
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

/**
 * 这场比赛是否该显示：「只看的人」里至少有一人在场 且 场上没有任何被排除的人。
 *
 * 多选「只看」取**并集**：选中的人里任意一位参加就显示（与「排除」取并集的口径一致，
 * 即「排除任意一人」就隐藏）。若改成交集（必须同场），多选几乎没有结果，
 * 与「我想看这几个人分别打哪些场」的诉求不符。
 */
export function matchVisible(m: any, filter: ScheduleFilter): boolean {
  const ids = matchPlayers(m).map((p) => p.id)
  if (filter.onlyPlayerIds.length > 0 && !ids.some((id) => filter.onlyPlayerIds.includes(id))) return false
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
 * 剪掉条件里已不在赛程中的选手。
 *
 * 赛中退赛会重排赛程（server 的 withdraw 会删掉未开打的轮次再重排），被选中的人
 * 从此不再出现在任何一场比赛里：他既筛不出比赛，面板里也没有对应的 chip 可以取消，
 * 条件会永远卡在集合里（摘要显示成 `#123`）。所以赛程加载出来后就把这类 id 剪掉。
 *
 * ⚠️ 调用方必须自己保证「赛程已经加载过」再调用：在 rounds 还没回来时用空名单调用，
 * 会把用户存的条件整个清空。见 ScheduleView 的 roundsLoaded 护栏。
 */
export function pruneFilter(f: ScheduleFilter, playerIds: number[]): ScheduleFilter {
  const ids = new Set(playerIds)
  const only = f.onlyPlayerIds.filter((id) => ids.has(id))
  const exclude = f.excludePlayerIds.filter((id) => ids.has(id))
  // 没有可剪的就原样返回（保持引用：调用方据此判断"有没有变化"来决定是否落盘/提示）
  if (only.length === f.onlyPlayerIds.length && exclude.length === f.excludePlayerIds.length) return f
  return normalizeFilter({ onlyPlayerIds: only, excludePlayerIds: exclude })
}

/**
 * 「只看我」是否正处于生效状态：条件正好是「只看我一个人」
 * （没有别的只看对象，也没有排除）。高亮与开关判断共用它。
 *
 * 不能只看「我在不在选中集合里」：多选下我可能只是集合的一员，那时按钮并不代表
 * 「只看我」。反过来说，这个判定为真时排除列表必定为空 —— 所以「再点一下取消筛选」
 * 不可能丢掉用户设过的排除（那些排除在第一次点击时就已经被收敛掉了）。
 */
export function isOnlyMeFilter(f: ScheduleFilter, uid: number | null): boolean {
  return uid != null
    && f.onlyPlayerIds.length === 1 && f.onlyPlayerIds[0] === uid
    && f.excludePlayerIds.length === 0
}

/**
 * 「只看我」点击后的下一个条件：**一键收敛**，不是往多选集合里加自己。
 * - 不是「只看我一个人」→ 变成只看我一个人（排除一起清掉，否则被别人挡住的、
 *   我自己参加的比赛仍然看不到）
 * - 已经正好是「只看我一个人」→ 取消筛选，回到全部比赛（保留开关键手感）
 */
export function nextOnlyMeFilter(f: ScheduleFilter, uid: number): ScheduleFilter {
  return isOnlyMeFilter(f, uid)
    ? emptyFilter()
    : normalizeFilter({ onlyPlayerIds: [uid], excludePlayerIds: [] })
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
