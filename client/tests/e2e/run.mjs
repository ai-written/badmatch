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
    const entry = { method: params.request.method, path: url.pathname, body: params.request.postData }
    this.requests.push(entry)
    const body = this.mock ? this.mock(url.pathname, entry) : {}
    this.send('Fetch.fulfillRequest', {
      requestId: params.requestId,
      responseCode: 200,
      responseHeaders: [{ name: 'Content-Type', value: 'application/json' }],
      body: Buffer.from(JSON.stringify(body ?? {})).toString('base64'),
    })
  }
  async enableMock(mock) {
    this.mock = mock
    this.requests = []
    await this.send('Fetch.enable', { patterns: [{ urlPattern: `${APP}/api/*`, requestStage: 'Request' }] })
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

// ---------------------------------------------------------------- 运行
const scenarios = [scenarioRefereeScoring, scenarioFixFlow, scenarioLayout, scenarioInviteLabel]

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
