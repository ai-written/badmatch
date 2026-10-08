/**
 * 对阵表筛选的纯函数单测：`cd client && npm run test:unit`（node --test）。
 *
 * 为什么单独放一份：这些规则（多选「只看」取并集、只看与排除互斥、旧格式迁移、
 * 脏数据收敛、未启用筛选时不重建数组）用 e2e 只能间接覆盖，改动 schedule.ts
 * 时靠这张分支表能立刻定位到具体规则。
 *
 * 依赖 Node 直接执行 .ts 的类型擦除（Node ≥ 22.18 / 23.6+ 默认开启）；
 * 更老的 Node 会在解析 import 时报错，请用较新的 Node 跑。
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import {
  normalizeFilter, matchVisible, emptyFilter, isFilterActive, filterRounds,
  pruneFilter, isOnlyMeFilter, nextOnlyMeFilter,
} from '../../src/utils/schedule.ts'

const P = (id) => ({ id, username: `p${id}` })
/** 一场比赛的 4 名选手（配对 id 与筛选无关，这里不需要） */
const M = (ids) => ({
  id: 1,
  pairing_a: { player_a: P(ids[0]), player_b: P(ids[1]) },
  pairing_b: { player_a: P(ids[2]), player_b: P(ids[3]) },
})

test('normalizeFilter：空值与非数字一律收敛成空条件', () => {
  assert.deepEqual(normalizeFilter(null), { onlyPlayerIds: [], excludePlayerIds: [], onlyMode: 'any' })
  assert.deepEqual(normalizeFilter(undefined), { onlyPlayerIds: [], excludePlayerIds: [], onlyMode: 'any' })
  assert.deepEqual(normalizeFilter({ onlyPlayerIds: 'x', excludePlayerIds: 'y' }),
    { onlyPlayerIds: [], excludePlayerIds: [], onlyMode: 'any' })
  // 重复项去重、非数字成员丢弃
  assert.deepEqual(
    normalizeFilter({ onlyPlayerIds: [1, '2', null, 3], excludePlayerIds: [4, 4, 'x'], onlyMode: 'any' }),
    { onlyPlayerIds: [1, 3], excludePlayerIds: [4], onlyMode: 'any' })
})

test('normalizeFilter：兼容旧版单选的 onlyPlayerId', () => {
  assert.deepEqual(normalizeFilter({ onlyPlayerId: 5, excludePlayerIds: [] }),
    { onlyPlayerIds: [5], excludePlayerIds: [], onlyMode: 'any' })
  assert.deepEqual(normalizeFilter({ onlyPlayerId: null, excludePlayerIds: [3] }),
    { onlyPlayerIds: [], excludePlayerIds: [3], onlyMode: 'any' })
})

test('normalizeFilter：新旧字段并存时，新字段为空才回落到旧值', () => {
  assert.deepEqual(normalizeFilter({ onlyPlayerIds: [7], onlyPlayerId: 5 }),
    { onlyPlayerIds: [7], excludePlayerIds: [], onlyMode: 'any' })
  assert.deepEqual(normalizeFilter({ onlyPlayerIds: [], onlyPlayerId: 5, excludePlayerIds: [9] }),
    { onlyPlayerIds: [5], excludePlayerIds: [9], onlyMode: 'any' })
})

test('normalizeFilter：同一人不能既「只看」又「排除」（以只看优先）', () => {
  assert.deepEqual(normalizeFilter({ onlyPlayerIds: [5], excludePlayerIds: [5, 6], onlyMode: 'any' }),
    { onlyPlayerIds: [5], excludePlayerIds: [6], onlyMode: 'any' })
  assert.deepEqual(normalizeFilter({ onlyPlayerId: 5, excludePlayerIds: [5] }),
    { onlyPlayerIds: [5], excludePlayerIds: [], onlyMode: 'any' })
})

test('normalizeFilter：NaN / 浮点会被 typeof 放行，必须丢弃', () => {
  assert.deepEqual(
    normalizeFilter({ onlyPlayerIds: [1, NaN, 1.5], excludePlayerIds: [NaN, 2, 2.5], onlyMode: 'any' }),
    { onlyPlayerIds: [1], excludePlayerIds: [2], onlyMode: 'any' })
  // 只剩非法值时视为「没有筛选」，否则会出现「筛选打开后一场都没有」
  assert.equal(isFilterActive(normalizeFilter({ onlyPlayerIds: [NaN] })), false)
})

test('emptyFilter 每次都给新数组（共享数组会被一处就地修改污染另一方）', () => {
  const a = emptyFilter()
  const b = emptyFilter()
  a.onlyPlayerIds.push(9)
  a.excludePlayerIds.push(9)
  assert.deepEqual([b.onlyPlayerIds, b.excludePlayerIds], [[], []])
})

test('matchVisible：多选「只看」取并集', () => {
  const f = normalizeFilter({ onlyPlayerIds: [5, 6] })
  assert.equal(matchVisible(M([1, 2, 3, 4]), f), false, '都不在场 → 隐藏')
  assert.equal(matchVisible(M([5, 1, 2, 3]), f), true, '任意一位选中者在 A 队即可见')
  assert.equal(matchVisible(M([1, 2, 6, 3]), f), true, '任意一位选中者在 B 队即可见')
  assert.equal(matchVisible(M([5, 6, 1, 2]), f), true)
})

test('matchVisible：排除取并集（任一被排除者在场就隐藏）', () => {
  const f = normalizeFilter({ excludePlayerIds: [5, 6] })
  assert.equal(matchVisible(M([1, 2, 3, 4]), f), true)
  assert.equal(matchVisible(M([5, 1, 2, 3]), f), false)
  assert.equal(matchVisible(M([1, 2, 6, 3]), f), false)
})

test('matchVisible：只看 + 排除叠加 = 两个条件同时满足', () => {
  const f = normalizeFilter({ onlyPlayerIds: [1, 2], excludePlayerIds: [3], onlyMode: 'any' })
  assert.equal(matchVisible(M([1, 3, 5, 6]), f), false, '有只看的人但也有被排除的人 → 隐藏')
  assert.equal(matchVisible(M([1, 5, 6, 7]), f), true)
  assert.equal(matchVisible(M([9, 5, 6, 7]), f), false, '没有只看的人 → 隐藏')
})

// ------------------------------------------------ 「或 / 且」两种只看模式

test('normalizeFilter：onlyMode 只认 all，其余一律回落 any', () => {
  assert.equal(normalizeFilter({ onlyPlayerIds: [1] }).onlyMode, 'any', '旧数据没有这个字段')
  assert.equal(normalizeFilter({ onlyPlayerIds: [1], onlyMode: 'all' }).onlyMode, 'all')
  assert.equal(normalizeFilter({ onlyPlayerIds: [1], onlyMode: 'ALL' }).onlyMode, 'any', '不认大小写变体')
  assert.equal(normalizeFilter({ onlyPlayerIds: [1], onlyMode: true }).onlyMode, 'any', '脏类型')
  assert.equal(normalizeFilter({ onlyPlayerIds: [1], onlyMode: null }).onlyMode, 'any')
  assert.equal(emptyFilter().onlyMode, 'any', '默认必须是「或」')
})

test('matchVisible：onlyMode=all 时要求所选的人全部同场', () => {
  const all = normalizeFilter({ onlyPlayerIds: [1, 3], onlyMode: 'all' })
  assert.equal(matchVisible(M([1, 2, 3, 4]), all), true, '1 和 3 都在场上')
  assert.equal(matchVisible(M([1, 2, 5, 6]), all), false, '只有 1 在')
  assert.equal(matchVisible(M([3, 2, 5, 6]), all), false, '只有 3 在')
  assert.equal(matchVisible(M([7, 8, 9, 10]), all), false, '都不在')
  // 同一场比赛换回「或」就是可见的 —— 差别只在模式
  assert.equal(matchVisible(M([1, 2, 5, 6]), normalizeFilter({ onlyPlayerIds: [1, 3] })), true)
})

test('matchVisible：只选一个人时两种模式完全等价', () => {
  for (const mode of ['any', 'all']) {
    const f = normalizeFilter({ onlyPlayerIds: [5], onlyMode: mode })
    assert.equal(matchVisible(M([5, 1, 2, 3]), f), true, mode)
    assert.equal(matchVisible(M([1, 2, 3, 4]), f), false, mode)
  }
})

test('matchVisible：all 模式选超过 4 人恒为空（2v2 一场只有 4 人）', () => {
  const f = normalizeFilter({ onlyPlayerIds: [1, 2, 3, 4, 5], onlyMode: 'all' })
  assert.equal(matchVisible(M([1, 2, 3, 4]), f), false)
  // 面板对这种情况有红字提示（draftOnlyIds.length > 4 时）
})

test('matchVisible：all 与排除叠加 = 同场且场上无被排除者', () => {
  const f = normalizeFilter({ onlyPlayerIds: [1, 3], excludePlayerIds: [2], onlyMode: 'all' })
  assert.equal(matchVisible(M([1, 3, 5, 6]), f), true)
  assert.equal(matchVisible(M([1, 2, 3, 4]), f), false, '2 被排除')
  assert.equal(matchVisible(M([1, 5, 6, 7]), f), false, '3 不在场')
})

test('pruneFilter / nextOnlyMeFilter 必须保留 onlyMode', () => {
  const all = normalizeFilter({ onlyPlayerIds: [1, 99], excludePlayerIds: [2, 98], onlyMode: 'all' })
  assert.equal(pruneFilter(all, [1, 2, 3, 4]).onlyMode, 'all', '剪枝不能把用户选的「且」重置成「或」')
  assert.deepEqual(pruneFilter(all, [1, 2, 3, 4]),
    { onlyPlayerIds: [1], excludePlayerIds: [2], onlyMode: 'all' })
  assert.equal(nextOnlyMeFilter(all, 7).onlyMode, 'all', '「只看我」收敛时保留模式')
  assert.equal(nextOnlyMeFilter(emptyFilter(), 7).onlyMode, 'any')
  // 剪枝没有变化时保持同一个引用（调用方据此判断要不要落盘/提示）
  const keep = normalizeFilter({ onlyPlayerIds: [1], onlyMode: 'all' })
  assert.equal(pruneFilter(keep, [1, 2]), keep)
})

test('filterRounds：未启用筛选时不重建数组，启用后保留轮次结构', () => {
  const rounds = [
    { id: 1, matches: [M([1, 2, 3, 4]), M([5, 6, 7, 8])] },
    { id: 2, matches: [M([1, 5, 2, 6])] },
  ]
  assert.equal(filterRounds(rounds, emptyFilter()), rounds)
  assert.deepEqual(
    filterRounds(rounds, normalizeFilter({ onlyPlayerIds: [5, 6] })).map((r) => r.matches.length),
    [1, 1])
  // 选中的人已不在赛程里（赛中退赛重排）→ 全部隐藏，页面走空态兜底；
  // 页面另有一层剪枝把这种 id 从条件里摘掉（见下面的 pruneFilter）
  assert.equal(
    filterRounds(rounds, normalizeFilter({ onlyPlayerIds: [99] })).flatMap((r) => r.matches).length,
    0)
})

test('pruneFilter：剪掉已不在赛程里的人，其余保留', () => {
  const f = normalizeFilter({ onlyPlayerIds: [1, 99], excludePlayerIds: [2, 98], onlyMode: 'any' })
  assert.deepEqual(pruneFilter(f, [1, 2, 3, 4]), { onlyPlayerIds: [1], excludePlayerIds: [2], onlyMode: 'any' })
})

test('pruneFilter：没有可剪的返回原引用（调用方据此决定是否落盘/提示用户）', () => {
  const f = normalizeFilter({ onlyPlayerIds: [1], excludePlayerIds: [2], onlyMode: 'any' })
  assert.equal(pruneFilter(f, [1, 2, 3]), f)
})

test('pruneFilter：拿到空名单就会清空条件 —— 所以调用方必须先确认赛程已加载成功', () => {
  // 这是接口契约：ScheduleView 用 roundsLoadedFor + 「名单非空」两道护栏保证
  // "拉取失败/零场次"时不会调用到这里
  const f = normalizeFilter({ onlyPlayerIds: [1] })
  assert.deepEqual(pruneFilter(f, []), { onlyPlayerIds: [], excludePlayerIds: [], onlyMode: 'any' })
})

test('pruneFilter：空条件 + 空名单也返回原引用（否则首屏会凭空弹「已移除」提示）', () => {
  const f = emptyFilter()
  assert.equal(pruneFilter(f, []), f)
  assert.equal(pruneFilter(f, [1, 2, 3]), f)
})

test('pruneFilter：只剪「只看」、只剪「排除」两条分支都要正确', () => {
  // 只剪 only（exclude 全都还在）
  assert.deepEqual(
    pruneFilter(normalizeFilter({ onlyPlayerIds: [1, 99], excludePlayerIds: [2], onlyMode: 'any' }), [1, 2, 3]),
    { onlyPlayerIds: [1], excludePlayerIds: [2], onlyMode: 'any' })
  // 只剪 exclude（only 全都还在）
  assert.deepEqual(
    pruneFilter(normalizeFilter({ onlyPlayerIds: [1], excludePlayerIds: [2, 99], onlyMode: 'any' }), [1, 2, 3]),
    { onlyPlayerIds: [1], excludePlayerIds: [2], onlyMode: 'any' })
  // 两边都要剪
  assert.deepEqual(
    pruneFilter(normalizeFilter({ onlyPlayerIds: [99], excludePlayerIds: [98], onlyMode: 'any' }), [1, 2, 3]),
    { onlyPlayerIds: [], excludePlayerIds: [], onlyMode: 'any' })
})

test('isOnlyMeFilter：只有「正好只看我一个人」才算生效', () => {
  const me = 7
  assert.equal(isOnlyMeFilter(normalizeFilter({ onlyPlayerIds: [7] }), me), true)
  assert.equal(isOnlyMeFilter(emptyFilter(), me), false, '没选不算')
  assert.equal(isOnlyMeFilter(normalizeFilter({ onlyPlayerIds: [7, 8] }), me), false, '还有别人不算')
  assert.equal(isOnlyMeFilter(normalizeFilter({ onlyPlayerIds: [8] }), me), false, '只看别人不算')
  assert.equal(
    isOnlyMeFilter(normalizeFilter({ onlyPlayerIds: [7], excludePlayerIds: [8], onlyMode: 'any' }), me), false,
    '还排除了别人就不算「只看我」（否则按钮高亮与实际结果不符）')
  assert.equal(isOnlyMeFilter(normalizeFilter({ onlyPlayerIds: [7] }), null), false, '不知道自己是谁不算')
})

test('isOnlyMeFilter 为真时排除必为空 —— 所以「再点一下取消」不会丢掉用户设过的排除', () => {
  const cases = [
    emptyFilter(),
    normalizeFilter({ onlyPlayerIds: [7] }),
    normalizeFilter({ onlyPlayerIds: [7, 8] }),
    normalizeFilter({ onlyPlayerIds: [7], excludePlayerIds: [9], onlyMode: 'any' }),
    normalizeFilter({ excludePlayerIds: [9] }),
  ]
  for (const f of cases) {
    if (isOnlyMeFilter(f, 7)) {
      assert.deepEqual(f.excludePlayerIds, [], `生效态不该带排除：${JSON.stringify(f)}`)
    }
  }
})

test('nextOnlyMeFilter：一键收敛成只看我一个人；已经是则取消筛选', () => {
  const me = 7
  // 选了别人 + 排除了别人 → 收敛成只看我，且排除被清掉
  const converged = nextOnlyMeFilter(
    normalizeFilter({ onlyPlayerIds: [8, 9], excludePlayerIds: [10], onlyMode: 'any' }), me)
  assert.deepEqual(converged, { onlyPlayerIds: [7], excludePlayerIds: [], onlyMode: 'any' })
  assert.equal(isOnlyMeFilter(converged, me), true, '收敛后必须处于生效态')

  // 从零开始点一下 → 只看我
  assert.deepEqual(nextOnlyMeFilter(emptyFilter(), me), { onlyPlayerIds: [7], excludePlayerIds: [], onlyMode: 'any' })

  // 已经生效再点 → 回到全部比赛
  assert.deepEqual(nextOnlyMeFilter(converged, me), { onlyPlayerIds: [], excludePlayerIds: [], onlyMode: 'any' })
})
