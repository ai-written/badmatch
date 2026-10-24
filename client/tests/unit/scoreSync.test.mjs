/**
 * 计分页「服务端旧比分不许覆盖本地新值」的闸门：`cd client && npm run test:unit`（node --test）。
 *
 * 这条规则是从一个真实投诉里抽出来的：正式环境在国外、带宽低，裁判点一下从 2 加到 3，
 * 界面有时会**跳回 2**。原因是本地乐观加分后 `pendingScore` 立刻被清空（请求已经发出），
 * 于是「已发出、还没落库」这段窗口里，任何更旧的服务端数据都能盖上来 ——
 * 观众点「加油」会触发 `support_updated` → 本页 `fetchMatch()`，这次 GET 可能在 PUT
 * 之前被服务端读到（2），却在 PUT 之后才回到浏览器。
 *
 * 依赖 Node 直接执行 .ts 的类型擦除（Node ≥ 22.18 / 23.6+ 默认开启）。
 */
import test from 'node:test'
import assert from 'node:assert/strict'

import { keepLocalScore, planScoreRetry, shouldAcceptServerScore } from '../../src/utils/scoreSync.ts'

const base = { pending: false, inFlight: false, requestEpoch: 0, currentEpoch: 0 }

test('空闲且没有代数变化：采纳服务端比分', () => {
  assert.equal(keepLocalScore(base), false)
  assert.equal(shouldAcceptServerScore(base), true)
})

test('本地有未提交的乐观改动（防抖窗口内）：保留本地', () => {
  // 连点 2→3→4 时，服务端此刻最多只到 2/3，盖上来就会「跳回去」
  assert.equal(keepLocalScore({ ...base, pending: true }), true)
})

test('记分请求已发出、服务端还没确认：保留本地', () => {
  // 这就是漏掉的那段窗口 —— 旧实现只在 pending 时保护，请求一发出就全裸奔
  assert.equal(keepLocalScore({ ...base, inFlight: true }), true)
})

test('响应发出后本地又落库过一次：这条是旧快照，丢掉', () => {
  // 慢 GET 场景：请求发出时代数 3，回来时已变成 4 → 读到的是落库前的数据
  assert.equal(keepLocalScore({ ...base, requestEpoch: 3, currentEpoch: 4 }), true)
})

test('响应发出后本地没有再写：采纳', () => {
  assert.equal(keepLocalScore({ ...base, requestEpoch: 4, currentEpoch: 4 }), false)
})

test('待提交 + 已发出 同时成立：依然保留本地', () => {
  assert.equal(keepLocalScore({ ...base, pending: true, inFlight: true }), true)
})

test('两个函数互为反面（调用点不要各写一套判断）', () => {
  const cases = [
    base,
    { ...base, pending: true },
    { ...base, inFlight: true },
    { ...base, requestEpoch: 2, currentEpoch: 3 },
    { ...base, requestEpoch: 3, currentEpoch: 3 },
  ]
  for (const c of cases) {
    assert.equal(shouldAcceptServerScore(c), !keepLocalScore(c), JSON.stringify(c))
  }
})

// ---- 版本冲突后的重试：把本地的「增量」叠到服务端最新值上，别覆盖 ----

test('两台设备各记一分：两分都不丢', () => {
  // 我看到 2 分、点了 +1（本地 3）；服务端已经变成 3（对方先记了）→ 重试要发 4
  const retry = planScoreRetry({ score_a: 3, score_b: 0 }, { score_a: 2, score_b: 0 }, { score_a: 3, score_b: 0 })
  assert.deepEqual(retry, { score_a: 4, score_b: 0 })
})

test('两边都记 b 队：同样各留一分', () => {
  const retry = planScoreRetry({ score_a: 5, score_b: 3 }, { score_a: 5, score_b: 2 }, { score_a: 5, score_b: 3 })
  assert.deepEqual(retry, { score_a: 5, score_b: 4 })
})

test('减分也按增量重算（不是把服务端值盖掉）', () => {
  // 我看到 5:2、点了 b 队 -1（本地 5:1）；服务端已是 5:3（对方在 b 队加了分）→ 5:2
  const retry = planScoreRetry({ score_a: 5, score_b: 1 }, { score_a: 5, score_b: 2 }, { score_a: 5, score_b: 3 })
  assert.deepEqual(retry, { score_a: 5, score_b: 2 })
})

test('服务端与基线一致时增量原样保留（等价于直接提交）', () => {
  const retry = planScoreRetry({ score_a: 9, score_b: 7 }, { score_a: 8, score_b: 7 }, { score_a: 8, score_b: 7 })
  assert.deepEqual(retry, { score_a: 9, score_b: 7 })
})

test('增量叠加不会算出负数', () => {
  const retry = planScoreRetry({ score_a: 0, score_b: 0 }, { score_a: 1, score_b: 0 }, { score_a: 0, score_b: 0 })
  assert.deepEqual(retry, { score_a: 0, score_b: 0 })
})
