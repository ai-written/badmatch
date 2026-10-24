/**
 * 「服务端给的比分能不能覆盖本地显示」的判定（纯函数，便于单测）。
 *
 * 为什么需要这道闸：记分是**乐观更新**——点一下先在本地加分，300ms 后防抖提交，
 * 服务端改完再广播 `match_updated`。原实现只在「本地还有未提交的改动」时跳过服务端值，
 * 而 `pendingScore` 在**发出请求的那一刻就被清空了**，于是「请求已发出、尚未落库」
 * 这整段窗口（弱网下几百 ms 到几秒）里，任何**更旧**的服务端状态都能盖上来：
 *
 * - 观众点「加油」会广播 `support_updated`，本页收到就 `fetchMatch()`；这次 GET 可能在
 *   我们的 PUT 之前被服务端读到（读到 2），却在 PUT 之后才回到浏览器 → 界面从 3 跳回 2；
 * - 每 30s 的校准拉取同理；
 * - WebSocket 积压/重连后补投的旧广播同理。
 *
 * 真实现象就是用户报的那句「点了从 2 加到 3，有时候又跳回 2」（正式环境在国外、带宽低，
 * 请求更慢，撞上的概率更高）。
 *
 * 判据只有一条：**本地没有比它更新的东西**才接受。
 * - `pending`：用户刚点的分还没提交（防抖窗口内）
 * - `inFlight`：提交已发出但服务端还没确认
 * - `requestEpoch`：这次读取/这条广播**发出时**本地确认过几次写入；
 *   `currentEpoch`：现在确认过几次。两者不等 → 期间本地已经落库过更新的分数，
 *   这份数据是旧的，丢掉。
 */
export interface ScoreSyncGuard {
  /** 本地还有未提交的乐观改动（防抖窗口内） */
  pending: boolean
  /** 记分请求已发出、服务端尚未确认 */
  inFlight: boolean
  /** 该服务端数据「发出」时的本地确认代数 */
  requestEpoch: number
  /** 当前的本地确认代数 */
  currentEpoch: number
}

/** 本地是否比这份服务端数据更新（更新就该保留本地值） */
export function keepLocalScore(g: ScoreSyncGuard): boolean {
  return g.pending || g.inFlight || g.requestEpoch !== g.currentEpoch
}

/** 反过来：可以采纳服务端比分 */
export function shouldAcceptServerScore(g: ScoreSyncGuard): boolean {
  return !keepLocalScore(g)
}

export interface ScorePair {
  score_a: number
  score_b: number
}

/**
 * 版本冲突（409）后的重试比分：**把本地增量叠到服务端最新比分上**，而不是覆盖它。
 *
 * 记分接口是「把比分设成绝对值」。两台设备同时记分时，后到的那次本来会把先到的那一分
 * 抹掉（丢分）。服务端用 `score_version` 发现基线过期就回 409 + 当前真实比分，
 * 这里算出「我原本想加多少」（local - base），再叠到服务端最新值上：
 *
 *   base = {2,0}  local = {3,0}（我加了 a 队一分）
 *   服务端已是 {3,0}（别人也加了 a 队一分）→ 重试值 {4,0} —— 两分都不丢
 *
 * 减分/修正同理（增量为负）。
 */
export function planScoreRetry(local: ScorePair, base: ScorePair, server: ScorePair): ScorePair {
  return {
    score_a: Math.max(0, server.score_a + (local.score_a - base.score_a)),
    score_b: Math.max(0, server.score_b + (local.score_b - base.score_b)),
  }
}
