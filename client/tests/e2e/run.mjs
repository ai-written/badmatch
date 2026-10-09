#!/usr/bin/env node
/**
 * badmatch 前端 e2e 回归（零依赖：Node + 系统已有的 Edge/Chrome + CDP）
 *
 *   cd client && npm run test:e2e          # 失败时退出码 1
 *   npm run test:e2e -- --headed           # 想看到窗口
 *
 * 覆盖（都是"曾经真出过问题"的地方，改完请跑一遍）：
 *   1 记分页：本场裁判在未结束比赛上能正常记分（控件在、加号可点、点一次=1 个请求）
 *   2 记分页：已结束的比赛默认只读；「修正比赛」→ 确认 → 加减只改本地（0 请求）→
 *     「确认修正」→ 恰好 1 个带 force_end 的请求（防误触 + 不重复提交）
 *   3 布局：各页首元素相对滚动容器顶边的距离（改动前逐像素基线，别随手改 CSS）
 *     + 对阵表首条高亮不被裁剪（裁剪边界到卡片顶 ≥ 11px）
 *   4 邀请码那行：副标题只占一行；文案变长时省略号截断、右侧邀请码仍完整
 *   7 对阵表筛选：「只看」多选的「或（任意一人）/ 且（必须同场）」开关（默认「或」、可持久化）
 *   8 对阵表右侧「裁 X」：被后来者顶替后显示新裁判、卸任后带「（已卸任）」、顶替前先确认
 *   9 赛事详情：进行中「追加比赛」（档位来自服务端、按倍数追加、非创建者看不到入口）
 *  10 管理面板：用户行（状态标签 + 操作收进动作面板）与禁用/恢复流程、权限收窄的菜单
 *
 * 说明：接口全部走 Fetch 域 mock（不依赖后端、不写库），所以随时可跑；
 * 后端权限/数据一致性仍靠真接口用例（见 README 的"验证"一节）。
 */
import { spawn } from 'node:child_process'
import { existsSync } from 'node:fs'
import { join } from 'node:path'
import { setTimeout as sleep } from 'node:timers/promises'

const APP = process.env.APP_URL || 'http://127.0.0.1:5199'
const CDP_PORT = Number(process.env.CDP_PORT || 9333)
const HEADED = process.argv.includes('--headed')
const VITE_PORT = new URL(APP).port || '5199'

// ---------------------------------------------------------------- 基础设施
function findBrowser() {
  const candidates = [
    process.env.BROWSER,
    'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
    'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe',
    'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
    '/usr/bin/microsoft-edge', '/usr/bin/google-chrome', '/usr/bin/chromium',
  ].filter(Boolean)
  return candidates.find((p) => existsSync(p))
}

const children = []
function track(child) {
  children.push(child)
  return child
}
function cleanup() {
  for (const c of children) { try { c.kill() } catch { /* 已退出 */ } }
}
process.on('exit', cleanup)
process.on('SIGINT', () => { cleanup(); process.exit(130) })

async function http(url, timeoutMs = 1500) {
  try {
    const r = await fetch(url, { signal: AbortSignal.timeout(timeoutMs) })
    return r.status
  } catch { return 0 }
}

async function ensureVite() {
  if (await http(`${APP}/`)) return false
  const root = new URL('../../', import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1')
  // 直接调 node + vite 的可执行 js：Windows 上 spawn npx.cmd 会 EINVAL（需要 shell:true）
  const viteBin = join(root, 'node_modules', 'vite', 'bin', 'vite.js')
  track(spawn(process.execPath, [viteBin, '--port', VITE_PORT, '--strictPort', '--host', '127.0.0.1'],
    { cwd: root, stdio: 'ignore' }))
  for (let i = 0; i < 30; i++) {
    await sleep(1000)
    if (await http(`${APP}/`)) return true
  }
  throw new Error(`dev server 未就绪：${APP}（先在 client/ 里 npm run dev？）`)
}

async function launchBrowser() {
  const bin = findBrowser()
  if (!bin) throw new Error('找不到 Edge/Chrome，请设置 BROWSER 环境变量指向浏览器可执行文件')
  track(spawn(bin, [
    `--remote-debugging-port=${CDP_PORT}`,
    `--user-data-dir=${process.env.TEMP || '/tmp'}/badmatch-e2e-profile`,
    '--no-first-run', '--no-default-browser-check', '--disable-gpu',
    ...(HEADED ? [] : ['--headless=new']),
    'about:blank',
  ], { stdio: 'ignore' }))
  for (let i = 0; i < 20; i++) {
    await sleep(500)
    if (await http(`http://127.0.0.1:${CDP_PORT}/json/version`)) return
  }
  throw new Error('浏览器调试端口未就绪')
}

// ---------------------------------------------------------------- CDP 客户端
class Page {
  constructor(ws) { this.ws = ws; this.id = 0; this.pending = new Map(); this.requests = [] }
  static async attach() {
    const list = await (await fetch(`http://127.0.0.1:${CDP_PORT}/json/list`)).json()
    const target = list.find((t) => t.type === 'page' && t.webSocketDebuggerUrl)
    if (!target) throw new Error('没有可用的页面 target')
    const ws = new WebSocket(target.webSocketDebuggerUrl)
    await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej })
    const page = new Page(ws)
    page.onMessage()
    await page.send('Page.enable')
    await page.send('Runtime.enable')
    await page.send('Emulation.setDeviceMetricsOverride',
      { width: 393, height: 796, deviceScaleFactor: 2, mobile: true })
    return page
  }
  onMessage() {
    this.ws.onmessage = (e) => {
      const m = JSON.parse(e.data)
      if (m.id && this.pending.has(m.id)) { this.pending.get(m.id)(m); this.pending.delete(m.id); return }
      if (m.method === 'Fetch.requestPaused') this.onRequest(m.params)
    }
  }
  send(method, params = {}) {
    return new Promise((res) => {
      const id = ++this.id
      this.pending.set(id, res)
      this.ws.send(JSON.stringify({ id, method, params }))
    })
  }
  /** 所有 /api/* 请求：先记录，再交给 scenario 的 mock 生成响应 */
  onRequest(params) {
    const url = new URL(params.request.url)
    if (!url.pathname.startsWith('/api/')) {
      this.send('Fetch.continueRequest', { requestId: params.requestId })
      return
    }
    const entry = { method: params.request.method, path: url.pathname, search: url.search, body: params.request.postData }
    this.requests.push(entry)
    const body = this.mock ? this.mock(url.pathname, entry) : {}
    // mock 可以返回 { __status: 4xx/5xx } 表示"这个接口失败"（例如验证赛程拉取失败时
    // 不会把用户存的筛选条件清掉）
    const status = (body && typeof body === 'object' && body.__status) || 200
    this.send('Fetch.fulfillRequest', {
      requestId: params.requestId,
      responseCode: status,
      responseHeaders: [{ name: 'Content-Type', value: 'application/json' }],
      body: Buffer.from(JSON.stringify(status === 200 ? (body ?? {}) : { detail: 'mock error' })).toString('base64'),
    })
  }
  async enableMock(mock) {
    this.mock = mock
    this.requests = []
    await this.send('Fetch.enable', { patterns: [{ urlPattern: `${APP}/api/*`, requestStage: 'Request' }] })
  }
  /** 在**每个新文档**里注入脚本。
   *
   *  Page.navigate 会换一个 JS realm：在当前文档里改 `Storage.prototype` 对"下一次加载"
   *  是无效的（只有注入到新文档才生效）。踩过一次，差点写出一条永远通过的假断言。
   *  返回 identifier，用完记得 removeInitScript。 */
  async addInitScript(source) {
    const r = await this.send('Page.addScriptToEvaluateOnNewDocument', { source })
    return r.result?.identifier
  }
  async removeInitScript(identifier) {
    await this.send('Page.removeScriptToEvaluateOnNewDocument', { identifier })
  }
  async goto(path, waitMs = 2600) {
    await this.send('Page.navigate', { url: `${APP}${path}` })
    await sleep(waitMs)
  }
  async ev(expression) {
    const r = await this.send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true })
    if (r.result?.exceptionDetails) throw new Error(`页面内异常：${r.result.exceptionDetails.text}`)
    return r.result?.result?.value
  }
  async setToken() {
    await this.goto('/', 1400)
    await this.ev(`localStorage.setItem('token','mock'); true`)
  }
  /** 按可见文案点击按钮（Vant 的 .van-button 会渲染成 button 元素） */
  async clickText(text) {
    const ok = await this.ev(`(() => {
      const b = [...document.querySelectorAll('button, .van-button')]
        .find(x => (x.textContent || '').trim() === ${JSON.stringify(text)})
      if (!b) return false
      b.click(); return true
    })()`)
    await sleep(700)
    return ok
  }
  async dialog() {
    return this.ev(`(() => {
      const d = document.querySelector('.van-dialog')
      if (!d) return null
      return {
        标题: (d.querySelector('.van-dialog__header')?.textContent || '').trim(),
        正文: (d.querySelector('.van-dialog__message')?.textContent || '').trim(),
      }
    })()`)
  }
  async confirmDialog() {
    await this.ev(`document.querySelector('.van-dialog__confirm')?.click(); true`)
    await sleep(900)
  }
  cancelDialog() {
    return this.ev(`document.querySelector('.van-dialog__cancel')?.click(); true`)
  }
}

// ---------------------------------------------------------------- 断言与场景
const results = []
function assert(name, ok, detail = '') {
  results.push({ name, ok: !!ok, detail })
  console.log(`   ${ok ? '✓' : '✗'} ${name}${ok || !detail ? '' : ` —— ${detail}`}`)
}

const P = (id, username) => ({ id, username, avatar: '' })
const matchPayload = (over = {}) => ({
  id: 101, round_id: 11, round_number: 1, status: 'ongoing', tournament_status: 'ongoing',
  score_a: 11, score_b: 3, winner_pairing_id: null, is_swapped: false, court_name: '1号场',
  started_at: '2026-09-29T08:00:00', duration_seconds: null, now: '2026-09-29T09:00:00',
  referee: null, active_referee: null, can_referee: false, my_support: null,
  support_a: 0, support_b: 0, support_a_users: [], support_b_users: [],
  pairing_a: { id: 1011, player_a: P(1, 'p1'), player_b: P(4, 'p4') },
  pairing_b: { id: 1012, player_a: P(5, 'p5'), player_b: P(6, 'p6') },
  ...over,
})

function scoreMock({ role = 'user', match }) {
  return (path) => {
    if (/auth\/me$/.test(path)) return { id: 1, username: '李祥', avatar: '', gender: 'M', role, invite_code: 'dd8f168e' }
    if (/\/matches\/\d+$/.test(path)) return match
    if (/auth\/stats/.test(path)) return { total_matches: 0, total_wins: 0, win_rate: 0, tournaments_played: 0 }
    if (/unread-count/.test(path)) return { count: 0 }
    if (/has-users/.test(path)) return { exists: true }
    if (/\/tournaments\/\d+$/.test(path)) {
      return { id: 2, creator_id: 1, title: 'T', location: 'L', status: match.tournament_status,
        max_participants: 8, courts: [], registered_count: 6, cancelled_count: 0, total_matches: 12,
        points_to_win: 11, server_now: '2026-09-29T08:00:00', is_registered: true,
        created_at: '2026-09-26T09:00:00' }
    }
    return {}
  }
}
const scorePuts = (page) => page.requests.filter((r) => r.method === 'PUT' && /\/score$/.test(r.path))

/** 场景 1：本场裁判在未结束比赛上的正常记分 */
async function scenarioRefereeScoring(page) {
  console.log('1) 记分页：本场裁判 + 赛事进行中 + 比赛未结束')
  const me = { id: 1 }
  await page.enableMock(scoreMock({
    role: 'user',
    match: matchPayload({ status: 'ongoing', active_referee: P(me.id, '李祥'), referee: P(me.id, '李祥') }),
  }))
  await page.setToken()
  await page.goto('/tournament/2/score/101?num=1')

  assert('记分控件可见', await page.ev(`!!document.querySelector('.controls')`))
  assert('加号可用（未被禁用）',
    await page.ev(`[...document.querySelectorAll('.wheel-btn.plus')].every(b => !b.disabled)`))
  assert('交换场地提示为「点击交换场地」',
    (await page.ev(`(document.querySelector('.swap-hint')?.textContent || '').trim()`)) === '点击交换场地')

  page.requests = []
  await page.ev(`document.querySelectorAll('.wheel-btn.plus')[0].click(); true`)
  await sleep(800)
  const puts = scorePuts(page)
  assert('点一次加号 = 恰好 1 个记分请求', puts.length === 1, `实际 ${puts.length}`)
  assert('记分请求是 PUT /score', /\/score$/.test(puts[0]?.path || ''))
}

/** 场景 2：已结束的比赛默认只读 + 确认式修正（防误触、不重复提交） */
async function scenarioFixFlow(page) {
  console.log('2) 记分页：超管 + 赛事进行中 + 该场已结束（确认式修正）')
  await page.enableMock(scoreMock({
    role: 'superadmin',
    match: matchPayload({ status: 'finished', winner_pairing_id: 1011 }),
  }))
  await page.setToken()
  await page.goto('/tournament/2/score/101?num=1')

  assert('默认没有记分控件（只读）', !(await page.ev(`!!document.querySelector('.controls')`)))
  assert('有「修正比赛」按钮',
    await page.ev(`[...document.querySelectorAll('.van-button')].some(b => (b.textContent||'').includes('修正比赛'))`))
  assert('默认不发任何请求', page.requests.filter((r) => r.method === 'PUT').length === 0)

  await page.clickText('修正比赛')
  const d1 = await page.dialog()
  assert('点「修正比赛」先弹确认框', d1?.标题 === '修正比赛结果', d1?.标题)
  await page.confirmDialog()
  assert('确认后进入修正模式', await page.ev(`!!document.querySelector('.controls')`))

  page.requests = []
  await page.ev(`document.querySelectorAll('.wheel-btn.plus')[0].click(); true`)
  await sleep(800)
  assert('修正模式里点加号 = 0 个请求（只改本地数字）', scorePuts(page).length === 0,
    `实际 ${scorePuts(page).length}`)
  assert('本地数字确实变了',
    (await page.ev(`document.querySelectorAll('.wheel .mid')[0].textContent.trim()`)) === '12')

  await page.clickText('确认修正')
  const d2 = await page.dialog()
  assert('点「确认修正」再弹一次确认框', d2?.标题 === '确认修正', d2?.标题)
  await page.confirmDialog()
  const puts = scorePuts(page)
  assert('确认后恰好 1 个请求（不重复提交）', puts.length === 1, `实际 ${puts.length}`)
  assert('请求带 force_end（重算胜负）', /"force_end":true/.test(puts[0]?.body || ''), puts[0]?.body)
  assert('提交后回到只读', !(await page.ev(`!!document.querySelector('.controls')`)))
}

/** 场景 2b：本场记录的裁判也能修正已结束的比赛（默认依然只读） */
async function scenarioMatchRefereeCanFix(page) {
  console.log('2b) 记分页：本场裁判 + 该场已结束（谁记的分谁能改）')
  await page.enableMock(scoreMock({
    role: 'user',
    match: matchPayload({ status: 'finished', winner_pairing_id: 1011, referee: P(1, '李祥') }),
  }))
  await page.setToken()
  await page.goto('/tournament/2/score/101?num=1')

  assert('默认仍只读（没有记分控件）', !(await page.ev(`!!document.querySelector('.controls')`)))
  assert('本场裁判能看到「修正比赛」',
    await page.ev(`[...document.querySelectorAll('.van-button')].some(b => (b.textContent||'').includes('修正比赛'))`))
  await page.clickText('修正比赛')
  const d = await page.dialog()
  assert('点「修正比赛」同样先弹确认框', d?.标题 === '修正比赛结果', d?.标题)
  await page.confirmDialog()
  assert('能进入修正模式', await page.ev(`!!document.querySelector('.controls')`))
}

/** 场景 3：各页首屏顶部留白基线 + 对阵表高亮不被裁剪 */
async function scenarioLayout(page) {
  console.log('3) 布局：各页首元素位置基线 + 对阵表首条高亮')
  const rounds = [{
    id: 11, round_number: 1, status: 'ongoing',
    matches: [matchPayload({ id: 101, can_referee: true })],
  }]
  const layoutMock = (path) => {
    if (/auth\/me$/.test(path)) return { id: 1, username: '李祥', avatar: '', gender: 'M', role: 'superadmin', invite_code: 'dd8f168e' }
    if (/\/rounds$/.test(path)) return rounds
    if (/\/rankings$/.test(path)) return { tournament_id: 2, tournament_title: 'T', rankings: [] }
    if (/\/registrations$/.test(path) || /\/cancellations$/.test(path)) return []
    if (/auth\/stats/.test(path)) return { total_matches: 0, total_wins: 0, win_rate: 0, tournaments_played: 0 }
    if (/unread-count/.test(path)) return { count: 0 }
    if (/notifications/.test(path)) return { items: [], total: 0, has_more: false }
    // 管理面板：必须是**非空**用户列表，否则页面渲染空态、没有刷新容器（这里曾误用 []）
    if (/admin\/users/.test(path)) {
      return [{ id: 1, username: '李祥', avatar: '', gender: 'M', role: 'superadmin', email: '', invited_by: null }]
    }
    if (/has-users/.test(path)) return { exists: true }
    if (/\/tournaments\?|\/tournaments$/.test(path)) return { items: [], total: 0, has_more: false }
    if (/\/tournaments\/\d+$/.test(path)) {
      return { id: 2, creator_id: 1, title: 'T', location: 'L', status: 'ongoing', max_participants: 8,
        courts: [], registered_count: 6, cancelled_count: 0, total_matches: 12, points_to_win: 11,
        server_now: '2026-09-29T08:00:00', is_registered: true, created_at: '2026-09-26T09:00:00' }
    }
    return {}
  }
  await page.enableMock(layoutMock)
  await page.setToken()
  // 对阵表的按人筛选会按赛事持久化（localStorage）。上一次运行留下的条件会让
  // 这里渲染出空列表 → 基线断言假失败，所以跑基线前先清掉。
  await page.ev(`localStorage.removeItem('schedule-filter:2'); true`)

  // 基线＝改动前的实测值，改布局时要么保持、要么同步改这里（改这里就要在 PR 里说明）
  const baseline = [
    ['首页', '/', 0], ['赛事详情', '/tournament/2', 10], ['对阵表', '/tournament/2/schedule', 12],
    ['积分榜', '/tournament/2/rankings', 0], ['我的', '/profile', 0],
    ['站内消息', '/notifications', 0], ['管理面板', '/admin', 12],
  ]
  const probe = `(() => {
    const pr = document.querySelector('.van-pull-refresh')
    if (!pr) return null
    const sc = pr.parentElement
    const inner = pr.querySelector('.pull-inner')
    let min = Infinity
    for (const el of inner.querySelectorAll('*')) {
      if (el.closest('.van-pull-refresh__head')) continue
      const r = el.getBoundingClientRect()
      if (!r.width && !r.height) continue
      if (r.top < min) min = r.top
    }
    return { offset: +(min - sc.getBoundingClientRect().top).toFixed(1) }
  })()`
  for (const [name, path, expected] of baseline) {
    await page.goto(path, 2200)
    // 带 tab / 多个接口的页面渲染慢一拍，探测重试几次再去断言（避免假失败）
    let r = null
    for (let i = 0; i < 6 && !r; i++) {
      r = await page.ev(probe)
      if (!r) await sleep(800)
    }
    assert(`${name}：首元素距滚动容器顶 = ${expected}px`, r && r.offset === expected, `实际 ${r?.offset}`)
  }

  // 对阵表首条高亮：卡片顶到裁剪边界的距离要 ≥ 11px（描边 2 + 光晕 14-3）
  await page.goto('/tournament/2/schedule', 1500)
  const target = await page.ev(`!!document.querySelector('.match-card.target')`)
  const clearance = await page.ev(`(() => {
    const card = document.querySelector('.match-card')
    const pr = document.querySelector('.van-pull-refresh')
    if (!card || !pr) return null
    const cs = getComputedStyle(pr)
    const clipTop = pr.getBoundingClientRect().top + (parseFloat(cs.borderTopWidth) || 0)
    return +(card.getBoundingClientRect().top - clipTop).toFixed(1)
  })()`)
  assert('首条高亮已生效（.target）', target)
  assert('高亮上方余量 ≥ 11px（不会被裁）', clearance >= 11, `实际 ${clearance}px`)
}

/** 场景 4：邀请码那行只占一行 + 文案变长时省略号截断、邀请码仍完整 */
async function scenarioInviteLabel(page) {
  console.log('4) 我的页：邀请码那行的排版与省略号兜底')
  await page.enableMock((path) => {
    if (/auth\/me$/.test(path)) return { id: 1, username: '李祥', avatar: '', gender: 'M', role: 'superadmin', invite_code: 'dd8f168e' }
    if (/auth\/stats/.test(path)) return { total_matches: 0, total_wins: 0, win_rate: 0, tournaments_played: 0 }
    if (/unread-count/.test(path)) return { count: 0 }
    if (/has-users/.test(path)) return { exists: true }
    return {}
  })
  await page.setToken()
  await page.goto('/profile', 2600)

  const info = await page.ev(`(() => {
    const cell = document.querySelector('.invite-code-cell')
    if (!cell) return null
    const title = cell.querySelector('.van-cell__title')
    const label = cell.querySelector('.van-cell__label')
    const value = cell.querySelector('.van-cell__value')
    const before = {
      标题minWidth: getComputedStyle(title).minWidth,
      一行: label.getClientRects().length === 1,
      邀请码: value.textContent.trim(),
    }
    label.textContent = '点击重新生成，旧码会立即失效，再点一次就会换一个新的邀请码'
    return { ...before,
      截断: label.scrollWidth > label.clientWidth + 1,
      邀请码仍完整: value.scrollWidth <= value.clientWidth + 1 }
  })()`)
  assert('找到邀请码那一行', !!info)
  assert('副标题只占一行', info?.一行)
  assert('标题有 min-width:0（省略号兜底是活的）', info?.标题minWidth === '0px', info?.标题minWidth)
  assert('文案变长时被省略号截断', info?.截断)
  assert('右侧邀请码仍完整可见', info?.邀请码仍完整, info?.邀请码)
}

/** 场景 5：对阵表按人筛选（只看某些人，可多选 / 排除一个或多个） */
async function scenarioScheduleFilter(page) {
  console.log('5) 对阵表：只看某些人（多选）/ 排除多人')
  // 第1场 p1 p2 | p3 p4 ；第2场 p5 p6 | p7 p8 ；第3场（第2轮）p1 p3 | p5 p7
  const rounds = [
    {
      id: 11, round_number: 1, status: 'ongoing', matches: [
        matchPayload({ id: 101, pairing_a: { id: 1011, player_a: P(1, 'p1'), player_b: P(2, 'p2') }, pairing_b: { id: 1012, player_a: P(3, 'p3'), player_b: P(4, 'p4') } }),
        matchPayload({ id: 102, status: 'pending', pairing_a: { id: 1021, player_a: P(5, 'p5'), player_b: P(6, 'p6') }, pairing_b: { id: 1022, player_a: P(7, 'p7'), player_b: P(8, 'p8') } }),
      ],
    },
    {
      id: 12, round_number: 2, status: 'pending', matches: [
        matchPayload({ id: 103, status: 'pending', pairing_a: { id: 1031, player_a: P(1, 'p1'), player_b: P(3, 'p3') }, pairing_b: { id: 1032, player_a: P(5, 'p5'), player_b: P(7, 'p7') } }),
      ],
    },
  ]
  await page.enableMock((path) => {
    if (/auth\/me$/.test(path)) return { id: 1, username: 'p1', avatar: '', gender: 'M', role: 'user', invite_code: '' }
    if (/\/rounds$/.test(path)) return rounds
    if (/unread-count/.test(path)) return { count: 0 }
    if (/has-users/.test(path)) return { exists: true }
    return {}
  })
  await page.setToken()
  // 筛选条件按赛事持久化（localStorage），上一次运行留下的条件会污染本场景
  await page.ev(`localStorage.removeItem('schedule-filter:2'); true`)
  await page.goto('/tournament/2/schedule', 2200)

  const shown = () => page.ev(`[...document.querySelectorAll('.match-card .match-num')].map(e => e.textContent.trim())`)
  const count = () => page.ev(`document.querySelectorAll('.match-card').length`)
  const summary = () => page.ev(`(document.querySelector('.filter-text')?.textContent || '').trim()`)
  // 页面内的点击统一走这里：元素不存在或事件处理里抛错都返回字符串而不是抛异常，
  // 否则一次失败会让整个场景中断，看不到后面的现象
  const clickEl = (sel) => page.ev(`(() => {
    try {
      const el = document.querySelector(${JSON.stringify(sel)})
      if (!el) return 'NOTFOUND'
      el.click(); return 'OK'
    } catch (e) { return 'ERR:' + (e && e.message) }
  })()`)
  const clickTextSafe = (txt) => page.ev(`(() => {
    try {
      const b = [...document.querySelectorAll('button, .van-button')].find(x => (x.textContent || '').trim() === ${JSON.stringify(txt)})
      if (!b) return 'NOTFOUND'
      b.click(); return 'OK'
    } catch (e) { return 'ERR:' + (e && e.message) }
  })()`)
  const pickIn = async (row, txt) => {
    const r = await page.ev(`(() => {
      try {
        const row = document.querySelectorAll('.filter-panel-body .pick-row')[${row}]
        if (!row) return 'NOROW'
        const el = [...row.querySelectorAll('.pick')].find(e => (e.textContent || '').trim() === ${JSON.stringify(txt)})
        if (!el) return 'NOTFOUND'
        el.click(); return 'OK'
      } catch (e) { return 'ERR:' + (e && e.message) }
    })()`)
    await sleep(220)
    return r
  }
  const openPanel = async () => { const r = await clickEl('.filter-chip'); await sleep(600); return r }
  const confirm = async () => { const r = await clickTextSafe('确定'); await sleep(600); return r }
  const clear = async () => { const r = await clickEl('.filter-mini.clear'); await sleep(700); return r }

  assert('默认显示全部 3 场', (await count()) === 3, `实际 ${await count()}`)
  // 直接打开对阵表也要认得自己（本页原先漏了 auth.fetchMe()，「我」角标一直是空的）
  assert('直接打开对阵表会加载自己的身份（「我」角标）',
    (await page.ev(`document.querySelectorAll('.me-badge').length`)) === 2,
    `实际 ${await page.ev(`document.querySelectorAll('.me-badge').length`)}`)

  assert('能打开筛选面板', (await openPanel()) === 'OK')
  assert('筛选面板已打开', await page.ev(`!!document.querySelector('.filter-panel-body')`))
  assert('候选人是「全部」+ 8 名选手', (await page.ev(`document.querySelectorAll('.filter-panel-body .pick-row')[0].querySelectorAll('.pick').length`)) === 9)

  // 只看 p5：第2场 + 第3场（p1 那场不该出现）
  assert('能选中「只看 p5」', (await pickIn(0, 'p5')) === 'OK')
  assert('点确定能生效', (await confirm()) === 'OK')
  assert('只看某人：只剩他参加的比赛', JSON.stringify(await shown()) === JSON.stringify(['第2场', '第3场']), JSON.stringify(await shown()))
  assert('筛选栏摘要显示条件', (await summary()) === '只看 p5', await summary())

  // 「只看」的人不能被同时排除：点它应当无效
  await openPanel()
  await pickIn(1, 'p5')
  await confirm()
  assert('「只看」的人无法被同时排除（自相矛盾的条件被挡住）', (await count()) === 2, `实际 ${await count()}`)

  // 多选「只看」：先取消已选的 p5，再选 p2 + p6（p2 只在第1场、p6 只在第2场）
  // 语义是并集：选中的人里任意一位参加就显示，所以第3场（p1/p3/p5/p7）不该出现
  await openPanel()
  assert('再点一次已选的人可以取消选中', (await pickIn(0, 'p5')) === 'OK')
  await pickIn(0, 'p2')
  await pickIn(0, 'p6')
  assert('点确定能生效（多选只看）', (await confirm()) === 'OK')
  assert('多选「只看」取并集', JSON.stringify(await shown()) === JSON.stringify(['第1场', '第2场']), JSON.stringify(await shown()))
  assert('摘要列出两个人', (await summary()) === '只看 p2、p6', await summary())

  // 多选状态下，「只看」里的人同样不能被排除（点它无效，结果不变，但要有明确提示）
  await openPanel()
  assert('多选时「只看」的人仍被挡在排除之外', (await pickIn(1, 'p2')) === 'OK')
  assert('被挡住时给出提示（而不是点了没反应）',
    (await page.ev(`(document.querySelector('.van-toast__text')?.textContent || '').trim()`)) === '「p2」已在「只看」里，不能再排除')
  await confirm()
  assert('排除未生效：结果仍是那 2 场', JSON.stringify(await shown()) === JSON.stringify(['第1场', '第2场']), JSON.stringify(await shown()))

  // 叠加：只看 p1 + 排除 p2 —— 第1场有 p2 被排掉，第3场同时满足
  await openPanel()
  await clickTextSafe('重置')
  await pickIn(0, 'p1')
  await pickIn(1, 'p2')
  assert('点确定能生效（叠加条件）', (await confirm()) === 'OK')
  assert('只看 + 排除叠加生效', JSON.stringify(await shown()) === JSON.stringify(['第3场']), JSON.stringify(await shown()))

  // 反方向：把已被排除的人再选进「只看」→ 自动从排除里摘掉（否则条件自相矛盾、结果必空）
  // 当前状态：只看 p1、排除 p2；这里在「只看」行点 p2
  await openPanel()
  await pickIn(0, 'p2')
  await confirm()
  assert('选进「只看」的人会自动从排除里摘掉', JSON.stringify(await shown()) === JSON.stringify(['第1场', '第3场']), JSON.stringify(await shown()))
  assert('摘掉后摘要不再显示排除', (await summary()) === '只看 p1、p2', await summary())

  // 多选「只看」+ 排除的摘要叠加格式（两处条件同时存在时才走这个分支）
  await openPanel()
  await pickIn(1, 'p6')
  await confirm()
  assert('多选只看 + 排除：摘要同时体现两者', (await summary()) === '只看 p1、p2 · 排除 1 人', await summary())
  assert('多选只看 + 排除：两者取交集', JSON.stringify(await shown()) === JSON.stringify(['第1场', '第3场']), JSON.stringify(await shown()))

  // 快速「只看我」（当前登录用户 p1）
  assert('清除筛选', (await clear()) === 'OK')
  assert('清除后回到全部比赛', (await count()) === 3, `实际 ${await count()}`)
  const onlyMe = await clickEl('.filter-mini')
  await sleep(700)
  assert('「只看我」一键筛选', onlyMe === 'OK' && JSON.stringify(await shown()) === JSON.stringify(['第1场', '第3场']),
    `${onlyMe} ${JSON.stringify(await shown())}`)

  // 「只看我」= 一键收敛成「只看我一个人」（不是往多选集合里加自己）
  const onlyMeOn = () => page.ev(`document.querySelectorAll('.filter-mini.on').length`)
  assert('「只看我」生效时高亮', (await onlyMeOn()) === 1, `实际 ${await onlyMeOn()}`)
  await openPanel()
  await pickIn(0, 'p2')   // 再选一个人
  await pickIn(1, 'p6')   // 同时排除一个人
  await confirm()
  assert('多选别人 + 排除后，「只看我」不再是生效状态', (await onlyMeOn()) === 0, `实际 ${await onlyMeOn()}`)
  const onlyMe2 = await clickEl('.filter-mini')
  await sleep(700)
  assert('再点「只看我」＝收敛成只看我一个人（其他条件一起清掉）',
    onlyMe2 === 'OK' && JSON.stringify(await shown()) === JSON.stringify(['第1场', '第3场']),
    `${onlyMe2} ${JSON.stringify(await shown())}`)
  assert('收敛后摘要只剩我一个人', (await summary()) === '只看 p1', await summary())
  assert('收敛后「只看我」重新高亮', (await onlyMeOn()) === 1, `实际 ${await onlyMeOn()}`)
  const onlyMe3 = await clickEl('.filter-mini')
  await sleep(700)
  assert('再点一下「只看我」＝取消筛选回到全部比赛',
    onlyMe3 === 'OK' && (await count()) === 3, `${onlyMe3} 实际 ${await count()}`)

  // 三人以上：摘要只列第一个 + 总数（筛选栏固定一行，截断后仍要能看出选了几个人）
  await openPanel()
  await pickIn(0, 'p2')
  await pickIn(0, 'p3')
  await pickIn(0, 'p6')
  await confirm()
  assert('只选 3 人时摘要显示「等 N 人」', (await summary()) === '只看 p2 等 3 人', await summary())
  assert('3 人并集：三个人参加过的比赛都在', JSON.stringify(await shown()) === JSON.stringify(['第1场', '第2场', '第3场']), JSON.stringify(await shown()))

  // 摘要里的名字按**面板顺序**（赛程出场序）排，而不是点选顺序：上面按 p2→p3→p6 点的，这里倒过来点
  await openPanel()
  await clickTextSafe('重置')
  await pickIn(0, 'p6')
  await pickIn(0, 'p2')
  await confirm()
  assert('摘要按面板顺序排列（与点选顺序无关）', (await summary()) === '只看 p2、p6', await summary())
  assert('倒序点选不影响结果', JSON.stringify(await shown()) === JSON.stringify(['第1场', '第2场']), JSON.stringify(await shown()))

  // 重置后只做多选排除：p5 与 p6 都在第2场，第3场含 p5
  await openPanel()
  await clickTextSafe('重置')
  await pickIn(1, 'p5')
  await pickIn(1, 'p6')
  await confirm()
  assert('排除多人：含任一被排除者的比赛全部隐藏', JSON.stringify(await shown()) === JSON.stringify(['第1场']), JSON.stringify(await shown()))

  // 空结果要有专门的空态与一键清除（不能显示成「已完成比赛已全部收起」）
  await openPanel()
  await pickIn(1, 'p1')
  await confirm()
  assert('筛选后无比赛时显示专用空态',
    (await page.ev(`(document.querySelector('.van-empty__description')?.textContent || '').trim()`)) === '没有符合筛选条件的比赛')
  assert('空态里的「清除筛选」可用', (await clickTextSafe('清除筛选')) === 'OK')
  assert('清除后恢复全部比赛', (await count()) === 3, `实际 ${await count()}`)

  // 持久化：重新进入页面条件仍在
  await openPanel()
  await pickIn(0, 'p6')
  await confirm()
  assert('只看 p6 生效', JSON.stringify(await shown()) === JSON.stringify(['第2场']), JSON.stringify(await shown()))
  await page.goto('/tournament/2/schedule', 2000)
  assert('刷新后筛选条件仍生效', JSON.stringify(await shown()) === JSON.stringify(['第2场']), JSON.stringify(await shown()))
  assert('刷新后摘要也还在', (await summary()) === '只看 p6', await summary())

  // 兼容升级前存的单选格式（onlyPlayerId: number）：老用户手机里那份条件不该失效
  await page.ev(`localStorage.setItem('schedule-filter:2', JSON.stringify({ onlyPlayerId: 2, excludePlayerIds: [] })); true`)
  await page.goto('/tournament/2/schedule', 2000)
  assert('旧版单选条件仍生效（读取时迁移）', JSON.stringify(await shown()) === JSON.stringify(['第1场']), JSON.stringify(await shown()))
  assert('旧版条件摘要按新格式显示', (await summary()) === '只看 p2', await summary())

  // 旧版「只排除」条件（没有只看的人）同样要能迁移
  await page.ev(`localStorage.setItem('schedule-filter:2', JSON.stringify({ onlyPlayerId: null, excludePlayerIds: [5] })); true`)
  await page.goto('/tournament/2/schedule', 2000)
  assert('旧版「只排除」条件仍生效', JSON.stringify(await shown()) === JSON.stringify(['第1场']), JSON.stringify(await shown()))
  assert('旧版「只排除」摘要正确', (await summary()) === '已排除 1 人', await summary())

  // 收尾清干净：条件会持久化，不能留给后面的场景（布局基线里也挂了对阵表）
  await clear()
  assert('收尾清除后恢复全部比赛', (await count()) === 3, `实际 ${await count()}`)

  // 赛程里已经没有这个人（赛中退赛会重排赛程）：这类残留 id 必须在赛程加载后被剪掉，
  // 否则条件永远卡着 —— 摘要显示 `只看 #99`、面板里也没有 chip 可以取消
  await page.ev(`localStorage.setItem('schedule-filter:2', JSON.stringify({ onlyPlayerIds: [99], excludePlayerIds: [] })); true`)
  await page.goto('/tournament/2/schedule', 2200)
  assert('已不在赛程里的选中者被剪掉（回到全部比赛）', (await count()) === 3, `实际 ${await count()}`)
  assert('剪枝后摘要回到「全部比赛」', (await summary()) === '全部比赛', await summary())
  assert('剪枝后不残留持久化条件', (await page.ev(`localStorage.getItem('schedule-filter:2')`)) === null)

  // 存着一段解析不了的字符串（被外部写坏）：当作没有条件，并把坏键清掉，
  // 否则每次打开都要再失败一次，而且坏键会一直躺在存储里
  await page.ev(`localStorage.setItem('schedule-filter:2', '{oops'); true`)
  await page.goto('/tournament/2/schedule', 2000)
  assert('损坏的筛选条件被忽略（回到全部比赛）', (await count()) === 3, `实际 ${await count()}`)
  assert('损坏的筛选键被顺手清掉', (await page.ev(`localStorage.getItem('schedule-filter:2')`)) === null)

  // 存储不可写（Safari 无痕/配额满/被策略禁用）：旧格式的回写失败**不能**把已经读到的
  // 条件一起丢掉（此前 setItem 抛错会走到同一个 catch，直接返回空条件并顺手删掉存储）。
  // 覆盖必须注入到**新文档**才生效（见 addInitScript 的说明）。
  await page.ev(`localStorage.setItem('schedule-filter:2', JSON.stringify({ onlyPlayerId: 2, excludePlayerIds: [] })); true`)
  const initId = await page.addInitScript(`Storage.prototype.setItem = function () { throw new Error('quota exceeded') }`)
  await page.goto('/tournament/2/schedule', 2000)
  // 元断言：先证明"注入真的生效、setItem 确实抛错"，否则这条用例会退化成永远通过的假断言
  assert('注入生效：该文档里 localStorage.setItem 确实不可写',
    (await page.ev(`(() => { try { localStorage.setItem('__probe', '1'); return false } catch (e) { return true } })()`)) === true)
  assert('存储不可写时，已存的条件仍能读到（回写失败不牵连读取）',
    JSON.stringify(await shown()) === JSON.stringify(['第1场']), JSON.stringify(await shown()))
  await page.removeInitScript(initId)
  // 上面那个覆盖只对被注入的那个文档有效；换一个干净文档才能拿回原生 setItem，
  // 否则后续用例的 localStorage.setItem 会继续抛错
  await page.goto('/', 900)
  await page.ev(`localStorage.removeItem('schedule-filter:2'); true`)

  // 赛程**拉取失败**时绝不能剪枝：否则一次网络抖动就会把用户存的条件当成"人已不在赛程"
  // 而清掉（roundsLoaded 护栏的意义）。这里让 /rounds 返回 500 再进页面。
  await page.enableMock((path) => {
    if (/auth\/me$/.test(path)) return { id: 1, username: 'p1', avatar: '', gender: 'M', role: 'user', invite_code: '' }
    if (/\/rounds$/.test(path)) return { __status: 500 }
    if (/unread-count/.test(path)) return { count: 0 }
    if (/has-users/.test(path)) return { exists: true }
    return {}
  })
  await page.ev(`localStorage.setItem('schedule-filter:2', JSON.stringify({ onlyPlayerIds: [5], excludePlayerIds: [] })); true`)
  await page.goto('/tournament/2/schedule', 2200)
  const storedAfterFail = await page.ev(`localStorage.getItem('schedule-filter:2')`)
  assert('赛程拉取失败时不清掉用户存的筛选条件',
    typeof storedAfterFail === 'string' && storedAfterFail.includes('"onlyPlayerIds":[5]'), String(storedAfterFail))
  assert('拉取失败显示的是失败态（不是「暂无赛程」）',
    (await page.ev(`!!document.querySelector('.load-failed')`)) === true)
  await page.ev(`localStorage.removeItem('schedule-filter:2'); true`)
}

/** 场景 6：筛选对「已完成比赛」和「已结束赛事」同样生效 */
async function scenarioFilterWithFinished(page) {
  console.log('6) 对阵表筛选：含已完成比赛 / 赛事已结束')
  const mk = (over) => matchPayload({ tournament_status: 'ongoing', ...over })
  // 第1轮两场都已完成：默认会折叠较早的第1场，只留第2场
  const playing = [
    {
      id: 11, round_number: 1, status: 'ongoing', matches: [
        mk({
          id: 201, status: 'finished', score_a: 11, score_b: 5, winner_pairing_id: 2011,
          pairing_a: { id: 2011, player_a: P(1, 'p1'), player_b: P(2, 'p2') },
          pairing_b: { id: 2012, player_a: P(3, 'p3'), player_b: P(4, 'p4') },
        }),
        mk({
          id: 202, status: 'finished', score_a: 11, score_b: 9, winner_pairing_id: 2022,
          pairing_a: { id: 2021, player_a: P(5, 'p5'), player_b: P(6, 'p6') },
          pairing_b: { id: 2022, player_a: P(7, 'p7'), player_b: P(8, 'p8') },
        }),
      ],
    },
    {
      id: 12, round_number: 2, status: 'ongoing', matches: [
        mk({
          id: 203, status: 'ongoing', score_a: 3, score_b: 2,
          pairing_a: { id: 2031, player_a: P(1, 'p1'), player_b: P(3, 'p3') },
          pairing_b: { id: 2032, player_a: P(5, 'p5'), player_b: P(7, 'p7') },
        }),
        mk({
          id: 204, status: 'pending',
          pairing_a: { id: 2041, player_a: P(2, 'p2'), player_b: P(4, 'p4') },
          pairing_b: { id: 2042, player_a: P(6, 'p6'), player_b: P(8, 'p8') },
        }),
      ],
    },
  ]
  const ended = [
    {
      id: 11, round_number: 1, status: 'finished', matches: [
        mk({ id: 201, status: 'finished', tournament_status: 'finished', score_a: 11, score_b: 5, winner_pairing_id: 2011,
             pairing_a: { id: 2011, player_a: P(1, 'p1'), player_b: P(2, 'p2') },
             pairing_b: { id: 2012, player_a: P(3, 'p3'), player_b: P(4, 'p4') } }),
        mk({ id: 202, status: 'finished', tournament_status: 'finished', score_a: 11, score_b: 9, winner_pairing_id: 2022,
             pairing_a: { id: 2021, player_a: P(5, 'p5'), player_b: P(6, 'p6') },
             pairing_b: { id: 2022, player_a: P(7, 'p7'), player_b: P(8, 'p8') } }),
      ],
    },
    {
      id: 12, round_number: 2, status: 'finished', matches: [
        mk({ id: 203, status: 'finished', tournament_status: 'finished', score_a: 11, score_b: 8, winner_pairing_id: 2031,
             pairing_a: { id: 2031, player_a: P(1, 'p1'), player_b: P(3, 'p3') },
             pairing_b: { id: 2032, player_a: P(5, 'p5'), player_b: P(7, 'p7') } }),
        mk({ id: 204, status: 'finished', tournament_status: 'finished', score_a: 4, score_b: 11, winner_pairing_id: 2042,
             pairing_a: { id: 2041, player_a: P(2, 'p2'), player_b: P(4, 'p4') },
             pairing_b: { id: 2042, player_a: P(6, 'p6'), player_b: P(8, 'p8') } }),
      ],
    },
  ]
  let rounds = playing
  await page.enableMock((path) => {
    if (/auth\/me$/.test(path)) return { id: 1, username: 'p1', avatar: '', gender: 'M', role: 'user', invite_code: '' }
    if (/\/rounds$/.test(path)) return rounds
    if (/unread-count/.test(path)) return { count: 0 }
    if (/has-users/.test(path)) return { exists: true }
    return {}
  })
  const shown = () => page.ev(`[...document.querySelectorAll('.match-card .match-num')].map(e => e.textContent.trim())`)
  const clickEl = (sel) => page.ev(`(() => {
    try { const el = document.querySelector(${JSON.stringify(sel)}); if (!el) return 'NOTFOUND'; el.click(); return 'OK' }
    catch (e) { return 'ERR:' + (e && e.message) } })()`)
  const pickOnly = async (txt) => {
    await clickEl('.filter-chip'); await sleep(600)
    await page.ev(`(() => {
      const row = document.querySelectorAll('.filter-panel-body .pick-row')[0]
      const el = [...row.querySelectorAll('.pick')].find(e => e.textContent.trim() === ${JSON.stringify(txt)})
      el.click(); return true })()`)
    await page.ev(`(() => { const b = [...document.querySelectorAll('button, .van-button')].find(x => x.textContent.trim() === '确定'); b.click(); return true })()`)
    await sleep(700)
  }
  const clearFilter = async () => { await clickEl('.filter-mini.clear'); await sleep(700) }

  await page.setToken()
  await page.ev(`localStorage.removeItem('schedule-filter:2'); true`)

  // A) 赛事进行中 + 含两场已完成比赛
  await page.goto('/tournament/2/schedule', 2200)
  assert('A 默认折叠较早的已完成比赛（13 场→3 场这里 4→3）',
    JSON.stringify(await shown()) === JSON.stringify(['第2场', '第3场', '第4场']), JSON.stringify(await shown()))
  await pickOnly('p1')
  assert('A 只看 p1：已完成的第1场也被筛出来（折叠按筛选结果重算，不再隐藏它）',
    JSON.stringify(await shown()) === JSON.stringify(['第1场', '第3场']), JSON.stringify(await shown()))
  await clearFilter()

  // B) 赛事已结束：不再折叠，筛选照常生效
  rounds = ended
  await page.goto('/tournament/2/schedule', 2200)
  assert('B 赛事已结束时显示只读提示', await page.ev(`!!document.querySelector('.readonly-banner')`))
  assert('B 赛事已结束不再折叠，4 场全显示',
    JSON.stringify(await shown()) === JSON.stringify(['第1场', '第2场', '第3场', '第4场']), JSON.stringify(await shown()))
  await pickOnly('p5')
  assert('B 已结束赛事里筛选仍然生效（只看 p5 → 第2、3场）',
    JSON.stringify(await shown()) === JSON.stringify(['第2场', '第3场']), JSON.stringify(await shown()))
  await clearFilter()
  assert('B 清除后回到 4 场', JSON.stringify(await shown()) === JSON.stringify(['第1场', '第2场', '第3场', '第4场']), JSON.stringify(await shown()))
  assert('收尾已清掉持久化条件', (await page.ev(`localStorage.getItem('schedule-filter:2')`)) === null)
}

/** 场景 7：对阵表「只看」多选的匹配方式开关（默认「或」，可切「且（同场）」） */
async function scenarioScheduleOnlyMode(page) {
  console.log('7) 对阵表：只看多选的「或（任意一人）/ 且（必须同场）」开关')
  // 第1场 p1 p2 | p3 p4 ；第2场 p5 p6 | p7 p8 ；第3场（第2轮）p1 p3 | p5 p7
  // p2 与 p6 从不同场 → 用来区分「或」（看得见）与「且」（看不见）
  const rounds = [
    {
      id: 11, round_number: 1, status: 'ongoing', matches: [
        matchPayload({ id: 101, pairing_a: { id: 1011, player_a: P(1, 'p1'), player_b: P(2, 'p2') }, pairing_b: { id: 1012, player_a: P(3, 'p3'), player_b: P(4, 'p4') } }),
        matchPayload({ id: 102, status: 'pending', pairing_a: { id: 1021, player_a: P(5, 'p5'), player_b: P(6, 'p6') }, pairing_b: { id: 1022, player_a: P(7, 'p7'), player_b: P(8, 'p8') } }),
      ],
    },
    {
      id: 12, round_number: 2, status: 'pending', matches: [
        matchPayload({ id: 103, status: 'pending', pairing_a: { id: 1031, player_a: P(1, 'p1'), player_b: P(3, 'p3') }, pairing_b: { id: 1032, player_a: P(5, 'p5'), player_b: P(7, 'p7') } }),
      ],
    },
  ]
  await page.enableMock((path) => {
    if (/auth\/me$/.test(path)) return { id: 1, username: 'p1', avatar: '', gender: 'M', role: 'user', invite_code: '' }
    if (/\/rounds$/.test(path)) return rounds
    if (/unread-count/.test(path)) return { count: 0 }
    if (/has-users/.test(path)) return { exists: true }
    return {}
  })
  await page.setToken()
  await page.ev(`localStorage.removeItem('schedule-filter:2'); true`)
  await page.goto('/tournament/2/schedule', 2200)

  const count = () => page.ev(`document.querySelectorAll('.match-card').length`)
  const shown = () => page.ev(`[...document.querySelectorAll('.match-card .match-num')].map(e => e.textContent.trim())`)
  const summary = () => page.ev(`(document.querySelector('.filter-text')?.textContent || '').trim()`)
  const emptyText = () => page.ev(`(document.querySelector('.van-empty__description')?.textContent || '').trim()`)
  const activeMode = () => page.ev(`(document.querySelector('.mode-row .pick.on')?.textContent || '').trim()`)
  const clickEl = (sel) => page.ev(`(() => {
    try { const el = document.querySelector(${JSON.stringify(sel)}); if (!el) return 'NOTFOUND'; el.click(); return 'OK' }
    catch (e) { return 'ERR:' + (e && e.message) } })()`)
  const pickIn = async (row, txt) => {
    const r = await page.ev(`(() => {
      try {
        const row = document.querySelectorAll('.filter-panel-body .pick-row')[${row}]
        if (!row) return 'NOROW'
        const el = [...row.querySelectorAll('.pick')].find(e => (e.textContent || '').trim() === ${JSON.stringify(txt)})
        if (!el) return 'NOTFOUND'
        el.click(); return 'OK'
      } catch (e) { return 'ERR:' + (e && e.message) } })()`)
    await sleep(200)
    return r
  }
  const setMode = async (txt) => {
    const r = await page.ev(`(() => {
      try {
        const el = [...document.querySelectorAll('.mode-row .pick')].find(e => (e.textContent || '').trim() === ${JSON.stringify(txt)})
        if (!el) return 'NOTFOUND'
        el.click(); return 'OK'
      } catch (e) { return 'ERR:' + (e && e.message) } })()`)
    await sleep(200)
    return r
  }
  const openPanel = async () => { const r = await clickEl('.filter-chip'); await sleep(600); return r }
  const confirm = async () => { await page.clickText('确定'); await sleep(700) }
  const clear = async () => { await clickEl('.filter-mini.clear'); await sleep(700) }

  // 01. 默认必须是「或」（老用户升级后行为不变）
  await openPanel()
  assert('筛选面板默认选中「或（任意一人）」', (await activeMode()) === '或（任意一人）', await activeMode())
  assert('「或」和「且」两个开关都在', (await page.ev(`document.querySelectorAll('.mode-row .pick').length`)) === 2)

  // 02. 「或」：p2 与 p6 从不同场，但并集下两场都出来
  await pickIn(0, 'p2')
  await pickIn(0, 'p6')
  await confirm()
  assert('「或」：任一人参加就显示（第1场 + 第2场）',
    JSON.stringify(await shown()) === JSON.stringify(['第1场', '第2场']), JSON.stringify(await shown()))
  assert('「或」的摘要仍以「只看」开头', (await summary()) === '只看 p2、p6', await summary())

  // 03. 同一批人切成「且」→ 两人从不同场，一场都不该有
  await openPanel()
  assert('能切到「且（必须同场）」', (await setMode('且（必须同场）')) === 'OK')
  await confirm()
  assert('「且」：所选的人必须同场（p2、p6 不同场 → 0 场）', (await count()) === 0, `实际 ${await count()}`)
  assert('「且」的摘要以「同场」开头（一眼看出筛的是"必须一起打"）',
    (await summary()) === '同场 p2、p6', await summary())
  assert('「且」筛空时仍是专用空态', (await emptyText()) === '没有符合筛选条件的比赛', await emptyText())

  // 04. 「且」+ p3、p7：两人只在第3场同场
  //     （p3 还出现在第1场、p7 还出现在第2场，但那两场里只有他们中的一个）
  await openPanel()
  await pickIn(0, 'p2')
  await pickIn(0, 'p6')
  await pickIn(0, 'p3')
  await pickIn(0, 'p7')
  await confirm()
  assert('「且」：只剩两人真正同场的那一场', JSON.stringify(await shown()) === JSON.stringify(['第3场']), JSON.stringify(await shown()))
  // 同一批人在「或」下三场都会出现 —— 差别只来自开关
  await openPanel()
  await setMode('或（任意一人）')
  await confirm()
  assert('同一批人切回「或」：各自的比赛全都回来',
    JSON.stringify(await shown()) === JSON.stringify(['第1场', '第2场', '第3场']), JSON.stringify(await shown()))

  // 05. 「且」选超过 4 人：2v2 一场只有 4 人，提前给出红字提示（而不是让人对着空列表猜）
  await openPanel()
  await setMode('且（必须同场）')
  await pickIn(0, 'p4')
  await pickIn(0, 'p5')
  await pickIn(0, 'p1')
  assert('「且」选 5 人时给出红字提示', await page.ev(`!!document.querySelector('.filter-hint.warn')`))
  assert('提示文案说明为什么筛不出来',
    ((await page.ev(`(document.querySelector('.filter-hint.warn')?.textContent || '')`))).includes('筛不出任何比赛'))
  await page.clickText('重置')
  await sleep(400)
  assert('「重置」把匹配方式也收回默认「或」', (await activeMode()) === '或（任意一人）', await activeMode())
  await page.clickText('确定')
  await sleep(700)
  assert('重置后回到全部比赛', (await count()) === 3, `实际 ${await count()}`)

  // 06. 开关要跟着条件一起记住（localStorage），刷新后不能悄悄变回「或」
  await openPanel()
  await setMode('且（必须同场）')
  await pickIn(0, 'p3')
  await pickIn(0, 'p7')
  await confirm()
  await page.goto('/tournament/2/schedule', 2200)
  assert('刷新后仍是「且（同场）」', (await summary()) === '同场 p3、p7', await summary())
  assert('刷新后筛选结果也一致（只剩第3场）', JSON.stringify(await shown()) === JSON.stringify(['第3场']), JSON.stringify(await shown()))
  assert('持久化的条件里带上了 onlyMode',
    (await page.ev(`JSON.parse(localStorage.getItem('schedule-filter:2') || '{}').onlyMode`)) === 'all')

  await clear()
  assert('收尾清除后回到全部比赛', (await count()) === 3, `实际 ${await count()}`)
  assert('收尾已清掉持久化条件', (await page.ev(`localStorage.getItem('schedule-filter:2')`)) === null)
}

/** 场景 8：对阵表右侧那行「裁 X」：被顶替后必须显示新裁判 */
async function scenarioScheduleRefereeRow(page) {
  console.log('8) 对阵表：右侧「裁 X」在顶替 / 卸任后的显示 + 顶替确认')
  // 模拟后端在两次 claim-referee 之后的真实返回（referee_id 被后来者覆盖）：
  //   第1场：B 把 A 顶掉 → referee 与 active_referee 都指向 B
  //   第2场：裁判 C 主动卸任 → referee 保留 C，active_referee 为空
  //   第3场：还没人认领
  const rounds = [{
    id: 11, round_number: 1, status: 'ongoing',
    matches: [
      matchPayload({ id: 101, can_referee: true, referee: P(2, '裁判B'), active_referee: P(2, '裁判B') }),
      matchPayload({ id: 102, status: 'pending', can_referee: true, referee: P(3, '裁判C') }),
      matchPayload({ id: 103, status: 'pending', can_referee: true }),
    ],
  }]
  await page.enableMock((path) => {
    if (/auth\/me$/.test(path)) return { id: 1, username: 'p1', avatar: '', gender: 'M', role: 'user', invite_code: '' }
    if (/\/rounds$/.test(path)) return rounds
    if (/unread-count/.test(path)) return { count: 0 }
    if (/has-users/.test(path)) return { exists: true }
    if (/\/matches\/\d+$/.test(path)) return matchPayload({ id: 101 })
    return {}
  })
  await page.setToken()
  await page.ev(`localStorage.removeItem('schedule-filter:2'); true`)
  await page.goto('/tournament/2/schedule', 2200)

  const foot = (i) => page.ev(`(document.querySelectorAll('.match-card')[${i}]?.querySelector('.foot-ref')?.textContent || '').trim()`)
  const claimPosts = () => page.requests.filter((r) => r.method === 'POST' && /claim-referee$/.test(r.path))
  const clickRefBtn = (i) => page.ev(`(() => {
    try {
      const b = document.querySelectorAll('.match-card')[${i}]?.querySelector('.match-info .van-button')
      if (!b) return 'NOTFOUND'
      b.click(); return 'OK'
    } catch (e) { return 'ERR:' + (e && e.message) } })()`)

  assert('被顶替后：右侧那行显示的是**新**裁判（不是空白）', (await foot(0)) === '裁 裁判B', await foot(0))
  assert('被顶替后：新裁判不会被误标成「已卸任」', !(await foot(0)).includes('已卸任'), await foot(0))
  assert('主动卸任后：名字保留 + 标记「（已卸任）」', (await foot(1)) === '裁 裁判C（已卸任）', await foot(1))
  assert('无人认领：右侧没有「裁」那行', (await foot(2)) === '', await foot(2))
  assert('无人认领：仍有「裁判」按钮可认领',
    (await page.ev(`(document.querySelectorAll('.match-card')[2]?.querySelector('.match-info .van-button')?.textContent || '').trim()`)) === '裁判')

  // 点已有在任裁判那场的「裁判」按钮：必须先弹确认框写明要顶替谁
  assert('能点到第1场的「裁判」按钮', (await clickRefBtn(0)) === 'OK')
  await sleep(500)
  const d = await page.dialog()
  assert('顶替前先弹「顶替本场裁判」确认框', d?.标题 === '顶替本场裁判', d?.标题)
  assert('确认框写明现任裁判是谁', (d?.正文 || '').includes('裁判B'), d?.正文)
  await page.cancelDialog()
  await sleep(500)
  assert('取消后不发认领请求', claimPosts().length === 0, `实际 ${claimPosts().length}`)

  assert('再次点「裁判」', (await clickRefBtn(0)) === 'OK')
  await sleep(500)
  await page.confirmDialog()
  assert('确认顶替后恰好 1 个 claim-referee 请求', claimPosts().length === 1, `实际 ${claimPosts().length}`)
}

/** 场景 9：赛中追加比赛（开场次排少了 / 还有时间，再排几场） */
async function scenarioAddMatches(page) {
  console.log('9) 赛事详情：进行中的赛事追加比赛')
  const players = Array.from({ length: 8 }, (_, i) => ({
    id: i + 1, user_id: i + 1, username: `p${i + 1}`, avatar: '', is_active: true,
    created_at: '2026-09-26T09:00:00',
  }))
  // 服务端只给「每人新增场次相同」的档位：8 人 → step=2
  let options = {
    players: 8, step: 2, current_total: 22, remaining: 78,
    options: [
      { added: 2, per_person_added: 1, total: 24 },
      { added: 4, per_person_added: 2, total: 26 },
    ],
  }
  let detail = {
    id: 2, creator_id: 1, title: 'T', location: 'L', status: 'ongoing', max_participants: 8,
    courts: [], registered_count: 8, cancelled_count: 0, total_matches: 22, points_to_win: 11,
    server_now: '2026-09-29T08:00:00', is_registered: true, created_at: '2026-09-26T09:00:00',
  }
  await page.enableMock((path) => {
    if (/auth\/me$/.test(path)) return { id: 1, username: 'p1', avatar: '', gender: 'M', role: 'user', invite_code: '' }
    if (/extra-match-options$/.test(path)) return options
    if (/add-matches$/.test(path)) {
      detail = { ...detail, total_matches: detail.total_matches + 2 }
      return { ok: true, added: 2, requested: 2, total_matches: detail.total_matches, rounds: 1, per_person_added: 1 }
    }
    if (/\/registrations$/.test(path)) return players
    if (/\/tournaments\/2$/.test(path)) return detail
    if (/unread-count/.test(path)) return { count: 0 }
    if (/has-users/.test(path)) return { exists: true }
    return {}
  })
  await page.setToken()

  const btnTexts = () => page.ev(`[...document.querySelectorAll('.creator-block .van-button')].map(b => (b.textContent || '').trim())`)
  const toast = () => page.ev(`(document.querySelector('.van-toast__text')?.textContent || '').trim()`)

  await page.goto('/tournament/2', 2400)
  assert('进行中：创建者能看到「追加比赛」', (await btnTexts()).includes('追加比赛'), JSON.stringify(await btnTexts()))
  assert('「提前结束赛事」还在（只是多了一个按钮）', (await btnTexts()).includes('提前结束赛事'))

  assert('能打开追加比赛弹层', (await page.clickText('追加比赛')) === true)
  const title = await page.ev(`(document.querySelector('.picker-title')?.textContent || '').trim()`)
  assert('弹层标题写明在场人数', title.includes('8 人在场'), title)
  const cells = await page.ev(`[...document.querySelectorAll('.van-popup .van-cell__title')].map(e => e.textContent.trim())`)
  // Vant 把 label 嵌在 .van-cell__title 里面，所以这里用前缀匹配标题
  assert('档位来自服务端（只能是 2 的倍数）',
    cells.length === 2 && cells[0].startsWith('追加 2 场') && cells[1].startsWith('追加 4 场'),
    JSON.stringify(cells))
  const labels = await page.ev(`[...document.querySelectorAll('.van-popup .van-cell__label')].map(e => e.textContent.trim())`)
  assert('标注每人新增场次与追加后的总场次', labels[0] === '每人再打 1 场，共 24 场', labels[0])
  assert('说明为什么只能按倍数追加',
    (await page.ev(`(document.querySelector('.add-hint')?.textContent || '')`)).includes('倍数'))

  page.requests = []
  assert('点档位即提交', (await page.ev(`(() => {
    try {
      const cell = [...document.querySelectorAll('.van-popup .van-cell')].find(c => (c.textContent || '').includes('追加 2 场'))
      if (!cell) return 'NOTFOUND'
      cell.click(); return 'OK'
    } catch (e) { return 'ERR:' + (e && e.message) } })()`)) === 'OK')
  await sleep(900)
  const posts = page.requests.filter((r) => r.method === 'POST' && /add-matches$/.test(r.path))
  assert('恰好 1 个 add-matches 请求', posts.length === 1, `实际 ${posts.length}`)
  assert('请求体是所选场次', /"matches":2/.test(posts[0]?.body || ''), posts[0]?.body)
  assert('提示已追加', (await toast()) === '已追加 2 场比赛', await toast())
  // van-popup 关闭后 DOM 仍在（lazy-render 只挡首次渲染），所以要按"不可见"判断
  assert('弹层已关闭', await page.ev(`(() => {
    const el = document.querySelector('.add-hint')
    return !el || el.offsetParent === null })()`))
  assert('总场次刷新为新值（共 24 场）',
    await page.ev(`(document.body.textContent || '').includes('共 24 场')`))

  // 非创建者（也不是管理员）不该看到入口
  detail = { ...detail, creator_id: 2 }
  await page.goto('/tournament/2', 2200)
  assert('非创建者看不到「追加比赛」', !(await btnTexts()).includes('追加比赛'), JSON.stringify(await btnTexts()))

  // 在场人数不足 4 人：给明确提示，而不是弹一个空列表
  detail = { ...detail, creator_id: 1 }
  options = { players: 3, step: 0, current_total: 22, remaining: 78, options: [] }
  await page.goto('/tournament/2', 2200)
  await page.clickText('追加比赛')
  await sleep(400)
  assert('人数不足时提示原因', (await toast()) === '在场选手不足 4 人，无法再排比赛', await toast())
  assert('不会弹出空档位列表', !(await page.ev(`!!document.querySelector('.add-hint')`)))
}

/** 场景 10：管理面板用户行（状态标签 + 操作收进动作面板）与禁用/恢复流程 */
async function scenarioAdminUserActions(page) {
  console.log('10) 管理面板：用户行布局 + 禁用/恢复账号')
  const me = { id: 1, username: 'root', avatar: '', gender: 'M', role: 'superadmin', invite_code: '' }
  let users = [
    { id: 2, username: '张三', avatar: '', gender: 'M', role: 'user', is_active: true, invited_by: 1, invited_by_username: 'root' },
    { id: 3, username: '李四', avatar: '', gender: 'M', role: 'user', is_active: false, invited_by: 1, invited_by_username: 'root' },
  ]
  const mockFor = (who) => (path, entry) => {
    if (/auth\/me$/.test(path)) return who
    if (/admin\/(users|selectable-users)$/.test(path)) return users
    if (/set-active$/.test(path)) {
      const body = JSON.parse(entry?.body || '{}')
      users = users.map(u => (u.id === body.user_id ? { ...u, is_active: body.is_active } : u))
      return { ok: true, changed: true, user_id: body.user_id, is_active: body.is_active }
    }
    if (/unread-count/.test(path)) return { count: 0 }
    if (/has-users/.test(path)) return { exists: true }
    return {}
  }
  await page.enableMock(mockFor(me))
  await page.setToken()
  await page.goto('/admin', 2400)

  const rowText = (name) => page.ev(`(() => {
    const cell = [...document.querySelectorAll('.van-cell')].find(c => (c.textContent || '').includes(${JSON.stringify(name)}))
    return cell ? (cell.textContent || '').replace(/\\s+/g, ' ').trim() : ''
  })()`)
  const clickRow = (name) => page.ev(`(() => {
    try {
      const cell = [...document.querySelectorAll('.van-cell')].find(c => (c.textContent || '').includes(${JSON.stringify(name)}))
      if (!cell) return 'NOCELL'
      cell.click(); return 'OK'
    } catch (e) { return 'ERR:' + (e && e.message) } })()`)
  const sheetTitle = () => page.ev(`(document.querySelector('.van-action-sheet__header')?.textContent || '').trim()`)
  const sheetActions = () => page.ev(`[...document.querySelectorAll('.van-action-sheet__item')].map(e => (e.textContent || '').trim())`)
  const sheetVisible = () => page.ev(`[...document.querySelectorAll('.van-action-sheet')].some(el => getComputedStyle(el).display !== 'none')`)
  const sheetActionClick = async (text) => {
    const r = await page.ev(`(() => {
      try {
        const el = [...document.querySelectorAll('.van-action-sheet__item')].find(e => (e.textContent || '').trim() === ${JSON.stringify(text)})
        if (!el) return 'NOTFOUND'
        el.click(); return 'OK'
      } catch (e) { return 'ERR:' + (e && e.message) } })()`)
    await sleep(400)
    return r
  }
  const toast = () => page.ev(`(document.querySelector('.van-toast__text')?.textContent || '').trim()`)
  const setActivePosts = () => page.requests.filter((r) => r.method === 'POST' && /set-active$/.test(r.path))
  const moreIcons = () => page.ev(`document.querySelectorAll('.user-more').length`)

  // ---- 布局：行内只留状态，不再堆按钮 ----
  assert('已禁用的账号在行内标出来', (await rowText('李四')).includes('已禁用'), await rowText('李四'))
  assert('行内不再有按钮簇（全部收进动作面板）',
    (await page.ev(`document.querySelectorAll('.van-cell .van-button').length`)) === 0,
    `实际 ${await page.ev(`document.querySelectorAll('.van-cell .van-button').length`)}`)
  assert('每一行右侧有「…」入口', (await moreIcons()) === 2, `实际 ${await moreIcons()}`)
  assert('行内不再有单独一行「重置密码」', !(await rowText('张三')).includes('重置密码'))

  // ---- 超管的面板：四项，破坏性操作标红 ----
  assert('点整行能打开操作面板', (await clickRow('张三')) === 'OK')
  await sleep(500)
  assert('面板标题写明是谁', (await sheetTitle()).includes('张三'), await sheetTitle())
  const actions = await sheetActions()
  assert('超管看到全部四项（顺序固定）',
    JSON.stringify(actions) === JSON.stringify(['设为管理员', '禁用账号（禁止登录）', '重置密码', '删除用户']),
    JSON.stringify(actions))
  assert('破坏性操作标红', await page.ev(`(() => {
    const el = [...document.querySelectorAll('.van-action-sheet__item')].find(e => (e.textContent || '').includes('禁用账号'))
    if (!el) return false
    const color = el.style.color || el.querySelector('.van-action-sheet__name')?.style.color || ''
    return /238/.test(color)
  })()`))

  // ---- 取消不生效 ----
  page.requests = []
  assert('点「禁用账号」', (await sheetActionClick('禁用账号（禁止登录）')) === 'OK')
  const d = await page.dialog()
  assert('先弹确认框（标题写明是禁用）', d?.标题 === '确认禁用', d?.标题)
  assert('确认框写明后果（无法登录、数据保留）',
    (d?.正文 || '').includes('无法登录') && (d?.正文 || '').includes('保留'), d?.正文)
  await page.cancelDialog()
  await sleep(400)
  assert('取消后不发请求', setActivePosts().length === 0, `实际 ${setActivePosts().length}`)

  // ---- 确认禁用 ----
  page.requests = []
  assert('再次打开面板', (await clickRow('张三')) === 'OK')
  await sleep(500)
  assert('再点「禁用账号」', (await sheetActionClick('禁用账号（禁止登录）')) === 'OK')
  await page.confirmDialog()
  const posts = setActivePosts()
  assert('恰好 1 个 set-active 请求', posts.length === 1, `实际 ${posts.length}`)
  assert('请求体 = 禁用该用户',
    /"user_id":2/.test(posts[0]?.body || '') && /"is_active":false/.test(posts[0]?.body || ''),
    posts[0]?.body)
  assert('提示已禁用', (await toast()) === '已禁用', await toast())
  assert('刷新后该行显示已禁用', (await rowText('张三')).includes('已禁用'), await rowText('张三'))
  assert('面板已关闭', (await sheetVisible()) === false)

  // ---- 恢复 ----
  assert('打开李四的面板', (await clickRow('李四')) === 'OK')
  await sleep(500)
  assert('已禁用账号的面板里是「恢复账号」',
    (await sheetActions()).includes('恢复账号'), JSON.stringify(await sheetActions()))
  page.requests = []
  assert('点「恢复账号」', (await sheetActionClick('恢复账号')) === 'OK')
  await page.confirmDialog()
  const posts2 = setActivePosts()
  assert('恢复请求 is_active=true',
    posts2.length === 1 && /"is_active":true/.test(posts2[0]?.body || ''), posts2[0]?.body)
  assert('提示已恢复', (await toast()) === '已恢复', await toast())

  // ---- 普通 admin：只能管自己邀请的普通用户，菜单也随之收窄 ----
  users = [
    { id: 2, username: '张三', avatar: '', gender: 'M', role: 'user', is_active: false, invited_by: 9, invited_by_username: 'adm' },
    { id: 3, username: '李四', avatar: '', gender: 'M', role: 'user', is_active: true, invited_by: 1, invited_by_username: 'root' },
  ]
  await page.enableMock(mockFor({ ...me, id: 9, username: 'adm', role: 'admin' }))
  await page.goto('/admin', 2400)
  const listReqs = page.requests.filter((r) => r.path === '/api/auth/admin/selectable-users')
  assert('普通 admin 的名单请求带上 include_disabled=true',
    listReqs.some((r) => (r.search || '').includes('include_disabled=true')),
    JSON.stringify(listReqs.map((r) => r.search)))
  assert('普通 admin 也能看到已禁用账号（才能恢复）', (await rowText('张三')).includes('已禁用'), await rowText('张三'))
  assert('别人邀请的用户没有操作入口', (await moreIcons()) === 1, `实际 ${await moreIcons()}`)
  assert('点「李四」那一行不会弹面板',
    (await clickRow('李四')) === 'OK' && (await sheetVisible()) === false)
  assert('打开自己邀请的用户', (await clickRow('张三')) === 'OK')
  await sleep(500)
  const adminActions = await sheetActions()
  assert('普通 admin 的菜单里没有超管专属项（角色 / 重置密码）',
    !adminActions.includes('设为管理员') && !adminActions.includes('重置密码'), JSON.stringify(adminActions))
  assert('普通 admin 能恢复 / 删除自己邀请的人',
    adminActions.includes('恢复账号') && adminActions.includes('删除用户'), JSON.stringify(adminActions))
}

// ---------------------------------------------------------------- 运行
const scenarios = [scenarioRefereeScoring, scenarioFixFlow, scenarioMatchRefereeCanFix, scenarioLayout, scenarioInviteLabel, scenarioScheduleFilter, scenarioFilterWithFinished, scenarioScheduleOnlyMode, scenarioScheduleRefereeRow, scenarioAddMatches, scenarioAdminUserActions]

async function main() {
  console.log(`前端 e2e 回归 → ${APP}（headless=${!HEADED}）`)
  await ensureVite()
  await launchBrowser()
  const page = await Page.attach()
  let crashed = null
  for (const run of scenarios) {
    try { await run(page) } catch (e) { crashed = e; assert(`${run.name} 执行异常`, false, e.message) }
  }
  page.ws.close()
  cleanup()

  const failed = results.filter((r) => !r.ok)
  console.log(`\n共 ${results.length} 项断言，失败 ${failed.length} 项`)
  if (failed.length) {
    for (const f of failed) console.log(`  ✗ ${f.name}${f.detail ? ` —— ${f.detail}` : ''}`)
    process.exit(1)
  }
  console.log('全部通过 ✓')
  if (crashed) process.exit(1)
}

main().catch((e) => { cleanup(); console.error(`\n运行失败：${e.message}`); process.exit(1) })
