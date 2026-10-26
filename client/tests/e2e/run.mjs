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
 *   7 对阵表筛选：「只看」多选的「或（任意一人）/ 且（必须同场）/ 搭档（同一队）」开关（默认「或」、可持久化）
 *   8 对阵表右侧「裁 X」：被后来者顶替后显示新裁判、卸任后带「（已卸任）」、顶替前先确认
 *   9 赛事详情：进行中「追加比赛」与「重排赛程」（档位来自服务端、按倍数追加、
 *     重排要先确认且取消不发请求、非创建者看不到入口）
 *  10 管理面板：用户行（状态标签 + 操作收进动作面板）与禁用/恢复流程、权限收窄的菜单
 *  11 赛事详情：报名中「编辑赛事信息」（入口/权限/表单预填/最大人数不低于已报名人数/保存）
 *  12 创建页：与编辑页共用同一表单组件后，单场与上下场创建发出的 payload 仍与改动前一致
 *  13 赛事详情：一次性分配裁判（在场选手能点、已退赛/路人不能点、管理者仍然可以）
 *  14 记分页：弱网下「慢 GET 带回旧比分」不会把刚点的分覆盖回去（点了 3 不跳回 2）
 *  15 记分页：两台设备同时记分不丢分（服务端 409 乐观锁 + 客户端增量重试）
 *  16 访问控制：未登录访问积分榜 → 登录页（并记住原目标）；登录后正常打开
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
    const raw = this.mock ? this.mock(url.pathname, entry) : {}
    // mock 可以返回：
    //   { __status: 4xx/5xx }  表示"这个接口失败"（例如验证赛程拉取失败时不会清掉筛选条件）
    //   { __status: 4xx, __body: {...} }  失败时还要带自定义响应体（如 409 的当前比分）
    //   { __delayMs: 1200 }    表示"晚点再回这个响应"——模拟弱网里**慢请求**：
    //                          响应内容按**发出时**的数据生成，回来时可能已经过时
    const status = (raw && typeof raw === 'object' && raw.__status) || 200
    const delay = (raw && typeof raw === 'object' && raw.__delayMs) || 0
    const payload = status === 200 ? (raw ?? {}) : (raw?.__body ?? { detail: 'mock error' })
    const fulfill = () => this.send('Fetch.fulfillRequest', {
      requestId: params.requestId,
      responseCode: status,
      responseHeaders: [{ name: 'Content-Type', value: 'application/json' }],
      body: Buffer.from(JSON.stringify(payload)).toString('base64'),
    })
    if (delay > 0) setTimeout(fulfill, delay)
    else fulfill()
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

/** 按人数算「每人场次相同」的合法档位（与后端 fairness_step 同源：步长 = N/gcd(4,N)）。
 *  用它当 mock 的 /match-options 响应，前端就会像真服务端一样"按当前人数给档位"——
 *  「改完人数立刻保存」那条回归必须有它，否则 mock 永远返回旧档位、测不出对齐。 */
function matchOptionsFor(n) {
  const gcd = (a, b) => (b ? gcd(b, a % b) : a)
  const step = n / gcd(4, n)
  const options = []
  for (let m = step; m <= 30; m += step) options.push({ total: m, per_person: (4 * m) / n })
  return { options, per_person: (4 * step) / n }
}

// ---- 表单/导航小工具（编辑页与创建页共用同一个组件，两个场景都要用）----
/** 按 van-field 的标签定位那一行的表达式 */
const fieldByLabel = (label) => `[...document.querySelectorAll('.van-field')]
  .find(c => (c.querySelector('.van-field__label')?.textContent || '').trim() === ${JSON.stringify(label)})`
const toastText = (page) => page.ev(`(document.querySelector('.van-toast__text')?.textContent || '').trim()`)
const navTitleOf = (page) => page.ev(`(document.querySelector('.van-nav-bar__title')?.textContent || '').trim()`)
const hasFieldOf = (page, label) => page.ev(`[...document.querySelectorAll('.van-field__label')]
  .some(e => (e.textContent || '').trim() === ${JSON.stringify(label)})`)
/** 读值：日期/时间段是只读字段，Vant 渲染成 div，取 textContent */
const fieldValueOf = (page, label) => page.ev(`(() => {
  const cell = ${fieldByLabel(label)}
  if (!cell) return 'NOTFOUND'
  const el = cell.querySelector('input, textarea, .van-field__control')
  if (!el) return 'NOINPUT'
  return el.tagName === 'DIV' ? (el.textContent || '').trim() : el.value
})()`)
/** 写值：改 value 后派发 input/change，让 v-model 跟着更新 */
const setFieldOf = (page, label, value) => page.ev(`(() => {
  const cell = ${fieldByLabel(label)}
  if (!cell) return 'NOTFOUND'
  const input = cell.querySelector('input, textarea')
  if (!input) return 'NOINPUT'
  input.value = ${JSON.stringify(String(value))}
  input.dispatchEvent(new Event('input', { bubbles: true }))
  input.dispatchEvent(new Event('change', { bubbles: true }))
  return 'OK'
})()`)

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

/** 场景 7：对阵表「只看」多选的匹配方式开关（默认「或」，可切「且（同场）」「搭档（同一队）」） */
async function scenarioScheduleOnlyMode(page) {
  console.log('7) 对阵表：只看多选的「或（任意一人）/ 且（必须同场）/ 搭档（同一队）」开关')
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
  assert('「或」「且」「搭档」三个开关都在', (await page.ev(`document.querySelectorAll('.mode-row .pick').length`)) === 3)

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

  // 07. 「搭档（同一队）」：只看这两个人是不是队友
  //     第1场 p1 p2 | p3 p4 → p1、p2 是队友
  await openPanel()
  assert('能切到「搭档（同一队）」', (await setMode('搭档（同一队）')) === 'OK')
  await pickIn(0, 'p1')
  await pickIn(0, 'p2')
  await confirm()
  assert('「搭档」：p1、p2 是第1场的队友 → 只剩第1场',
    JSON.stringify(await shown()) === JSON.stringify(['第1场']), JSON.stringify(await shown()))
  assert('「搭档」的摘要以「搭档」开头（区别于「只看」「同场」）',
    (await summary()) === '搭档 p1、p2', await summary())

  // 同场但分属两队（第3场 p1 p3 | p5 p7 里 p3 与 p7 是对手）：
  // 「且（同场）」看得到，「搭档」必须看不到 —— 这是新开关存在的意义
  await openPanel()
  await pickIn(0, 'p1')
  await pickIn(0, 'p2')
  await pickIn(0, 'p3')
  await pickIn(0, 'p7')
  await confirm()
  assert('「搭档」：同场但互为对手 → 0 场（不是「且（同场）」）',
    (await count()) === 0, `实际 ${await count()}`)
  await openPanel()
  await setMode('且（必须同场）')
  await confirm()
  assert('同一对人在「且（同场）」下能看到那场（证明两个开关确实不同）',
    JSON.stringify(await shown()) === JSON.stringify(['第3场']), JSON.stringify(await shown()))

  // 选的人数不是 2 个时提前给红字提示（否则只会看到空列表）
  await openPanel()
  await setMode('搭档（同一队）')
  await pickIn(0, 'p7')
  assert('「搭档」选 1 个人时给出红字提示', await page.ev(`!!document.querySelector('.filter-hint.warn')`))
  assert('提示写明要正好选 2 个人',
    ((await page.ev(`(document.querySelector('.filter-hint.warn')?.textContent || '')`))).includes('正好选 2 个人'))
  await pickIn(0, 'p3')
  await pickIn(0, 'p5')
  await pickIn(0, 'p6')
  await pickIn(0, 'p1')   // 凑到 3 个人
  assert('「搭档」选 3 个人时也给提示', await page.ev(`!!document.querySelector('.filter-hint.warn')`))
  await confirm()
  assert('「搭档」选 3 个人筛不出比赛', (await count()) === 0, `实际 ${await count()}`)
  assert('人数不对时筛选栏摘要写明原因（不用打开面板就知道为什么空）',
    (await summary()) === '搭档 p1 等 3 人（需选 2 人）', await summary())

  // 08. 「搭档」也要跟着条件一起记住
  await openPanel()
  await page.clickText('重置')
  await sleep(400)
  await setMode('搭档（同一队）')
  await pickIn(0, 'p1')
  await pickIn(0, 'p2')
  await confirm()
  await page.goto('/tournament/2/schedule', 2200)
  assert('刷新后仍是「搭档」', (await summary()) === '搭档 p1、p2', await summary())
  assert('持久化的条件里 onlyMode = partner',
    (await page.ev(`JSON.parse(localStorage.getItem('schedule-filter:2') || '{}').onlyMode`)) === 'partner')

  await clear()
  assert('搭档条件清除后回到全部比赛', (await count()) === 3, `实际 ${await count()}`)

  // 09. 在「搭档」下点「只看我」：必须落回「或」——「搭档」要求正好 2 人，
  //     收敛成我一个人时如果还留着 partner，会一场都筛不出来（曾经就是这样）
  await openPanel()
  await setMode('搭档（同一队）')
  await pickIn(0, 'p1')
  await pickIn(0, 'p2')
  await confirm()
  assert('先摆好「搭档 p1、p2」', (await summary()) === '搭档 p1、p2', await summary())
  const clickOnlyMe = () => page.ev(`(() => {
    const el = [...document.querySelectorAll('.filter-bar .filter-mini')].find(e => (e.textContent || '').trim() === '只看我')
    if (!el) return 'NOTFOUND'
    el.click(); return 'OK'
  })()`)
  assert('「只看我」按钮在', (await clickOnlyMe()) === 'OK')
  await sleep(700)
  assert('「只看我」落回「或」：摘要变成「只看 p1」', (await summary()) === '只看 p1', await summary())
  assert('「只看我」能看自己参加的场次（不是空列表）',
    JSON.stringify(await shown()) === JSON.stringify(['第1场', '第3场']), JSON.stringify(await shown()))
  assert('持久化的 onlyMode 也跟着回到 any',
    (await page.ev(`JSON.parse(localStorage.getItem('schedule-filter:2') || '{}').onlyMode`)) === 'any')
  await clickOnlyMe()
  await sleep(700)
  assert('再点一次取消筛选，回到全部比赛', (await count()) === 3, `实际 ${await count()}`)
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
  console.log('9) 赛事详情：进行中的赛事追加比赛 / 重排赛程')
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
    // 一场都没开打 → 才给「重排赛程」入口（有比赛开打/打完就不给）
    started_match_count: 0,
  }
  await page.enableMock((path) => {
    if (/auth\/me$/.test(path)) return { id: 1, username: 'p1', avatar: '', gender: 'M', role: 'user', invite_code: '' }
    if (/extra-match-options$/.test(path)) return options
    if (/add-matches$/.test(path)) {
      detail = { ...detail, total_matches: detail.total_matches + 2 }
      return { ok: true, added: 2, requested: 2, total_matches: detail.total_matches, rounds: 1, per_person_added: 1 }
    }
    if (/reschedule$/.test(path)) {
      return { ok: true, players: 8, matches: 22, replaced_matches: 6, replaced_rounds: 3,
               released_referees: 2, total_matches: 22 }
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

  // 重排赛程：确认框要先说清"已打完的不动 + 裁判会被取消"，取消不发请求
  assert('进行中：创建者能看到「重排赛程」',
    (await btnTexts()).includes('重排赛程'), JSON.stringify(await btnTexts()))
  const reschedulePosts = () => page.requests.filter((r) => r.method === 'POST' && /reschedule$/.test(r.path))
  page.requests = []
  assert('能点「重排赛程」', (await page.clickText('重排赛程')) === true)
  await sleep(500)
  const rd = await page.dialog()
  assert('重排前先弹确认框', rd?.标题 === '重排赛程', rd?.标题)
  assert('确认框写清会整批换掉 + 裁判会被取消',
    (rd?.正文 || '').includes('整批换掉') && (rd?.正文 || '').includes('裁判'), rd?.正文)
  await page.cancelDialog()
  await sleep(500)
  assert('取消后不发重排请求', reschedulePosts().length === 0, `实际 ${reschedulePosts().length}`)

  assert('再次点「重排赛程」', (await page.clickText('重排赛程')) === true)
  await sleep(500)
  await page.confirmDialog()
  assert('确认后恰好 1 个 reschedule 请求', reschedulePosts().length === 1, `实际 ${reschedulePosts().length}`)
  assert('重排提示写明重排了多少场 + 几位裁判要重新认领',
    (await toast()) === '已重排 6 场，2 位裁判需重新认领', await toast())

  // 非创建者（也不是管理员）不该看到入口
  detail = { ...detail, creator_id: 2 }
  await page.goto('/tournament/2', 2200)
  assert('非创建者看不到「追加比赛」', !(await btnTexts()).includes('追加比赛'), JSON.stringify(await btnTexts()))
  assert('非创建者看不到「重排赛程」',
    !(await btnTexts()).includes('重排赛程'), JSON.stringify(await btnTexts()))

  // 已经开打（有人打了/正在打）：重排入口必须消失 —— 重排会把未开打的场次整批删掉重排
  detail = { ...detail, creator_id: 1, started_match_count: 2 }
  await page.goto('/tournament/2', 2200)
  assert('已有比赛开打时，创建者也看不到「重排赛程」',
    !(await btnTexts()).includes('重排赛程'), JSON.stringify(await btnTexts()))
  assert('开打后「追加比赛」仍在（那个入口允许赛中追加）',
    (await btnTexts()).includes('追加比赛'), JSON.stringify(await btnTexts()))

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

/** 场景 11：报名中赛事的「编辑赛事信息」（入口/权限/预填/人数下限/保存） */
async function scenarioEditTournament(page) {
  console.log('11) 赛事详情：报名中「编辑赛事信息」')
  const registrations = Array.from({ length: 5 }, (_, i) => ({
    id: i + 1, user_id: i + 1, username: `p${i + 1}`, avatar: '', is_active: true,
    created_at: '2026-10-10T09:00:00',
  }))
  let me = { id: 1, username: 'p1', avatar: '', gender: 'M', role: 'user', invite_code: '' }
  let detail = {
    id: 2, creator_id: 1, title: '10月16日养生局', description: '老备注', location: '奥体中心',
    start_date: '2026-10-16T19:00:00', end_date: '2026-10-16T21:00:00',
    max_participants: 8, status: 'open',
    courts: [{ id: 1, name: 'A1', sort_order: 0, time_slots: [{ id: 1, start_time: '19:00:00', end_time: '21:00:00' }] }],
    registered_count: 5, cancelled_count: 0, pending_referee_count: 0, started_match_count: 0,
    total_matches: 10, points_to_win: 11, registration_open_at: null,
    server_now: '2026-10-14T08:00:00', is_registered: false, created_at: '2026-10-10T09:00:00',
  }
  await page.enableMock((path, entry) => {
    if (/auth\/me$/.test(path)) return me
    if (/\/registrations$/.test(path)) return registrations
    if (/match-options\//.test(path)) return matchOptionsFor(Number(path.split('/').pop()))
    if (/unread-count/.test(path)) return { count: 0 }
    if (/has-users/.test(path)) return { exists: true }
    if (/\/tournaments\/2$/.test(path)) {
      if (entry.method === 'PUT') {
        const body = JSON.parse(entry.body || '{}')
        detail = {
          ...detail, ...body,
          courts: (body.courts || []).map((c, i) => ({ id: i + 1, ...c })),
        }
        return detail
      }
      return detail
    }
    return {}
  })
  await page.setToken()

  const btnTexts = () => page.ev(`[...document.querySelectorAll('.creator-block .van-button')].map(b => (b.textContent || '').trim())`)
  const navIcons = () => page.ev(`[...document.querySelectorAll('.nav-right .van-icon')].map(e => e.className)`)
  const toast = () => toastText(page)
  const navTitle = () => navTitleOf(page)
  const hasField = (label) => hasFieldOf(page, label)
  // 按标签找那一行的输入框/只读展示区（日期、时间段是只读字段，渲染成 div）
  const fieldValue = (label) => fieldValueOf(page, label)
  const setField = (label, value) => setFieldOf(page, label, value)
  const puts = () => page.requests.filter((r) => r.method === 'PUT' && /\/tournaments\/2$/.test(r.path))

  // ---- 报名中 + 创建者：入口可见 ----
  await page.goto('/tournament/2', 2400)
  assert('报名中：创建者能看到「编辑赛事信息」',
    (await btnTexts()).includes('编辑赛事信息'), JSON.stringify(await btnTexts()))
  assert('顶栏有编辑图标（与底部按钮同一个入口）',
    (await navIcons()).some(c => c.includes('van-icon-edit')), JSON.stringify(await navIcons()))

  // ---- 进编辑页：同一套表单，预填原有值 ----
  assert('点「编辑赛事信息」进编辑页', (await page.clickText('编辑赛事信息')) === true)
  await sleep(900)
  assert('路由是 /tournament/2/edit', (await page.ev('location.pathname')) === '/tournament/2/edit',
    await page.ev('location.pathname'))
  assert('标题栏是「编辑赛事」', (await navTitle()) === '编辑赛事', await navTitle())
  assert('赛事名称已预填', (await fieldValue('赛事名称')) === '10月16日养生局', await fieldValue('赛事名称'))
  assert('备注已预填', (await fieldValue('备注')) === '老备注', await fieldValue('备注'))
  assert('地点已预填', (await fieldValue('地点')) === '奥体中心', await fieldValue('地点'))
  assert('场地号已预填', (await fieldValue('场地号')) === 'A1', await fieldValue('场地号'))
  assert('最大人数已预填', String(await fieldValue('最大人数')) === '8', String(await fieldValue('最大人数')))
  assert('日期已回填', (await fieldValue('日期')).includes('10月16日'), await fieldValue('日期'))
  assert('时间段已回填', (await fieldValue('时间段')) === '19:00 ~ 21:00', await fieldValue('时间段'))
  assert('编辑页没有「上下场」（那是"一次建两条赛事"）', (await hasField('上下场')) === false)
  assert('编辑页没有「默认参赛人员」（预选只在创建时生效）', (await hasField('默认参赛人员')) === false)

  // ---- 最大人数下限：低于已报名人数（5）时前端直接拦住，不发请求 ----
  page.requests = []
  assert('把最大人数改成 4', (await setField('最大人数', 4)) === 'OK')
  assert('点「保存修改」', (await page.clickText('保存修改')) === true)
  await sleep(600)
  assert('低于已报名人数：一个 PUT 都不发', puts().length === 0, `实际 ${puts().length}`)
  assert('提示写明当前已报名人数', (await toast()) === '最大人数不能小于已报名人数（5 人）', await toast())
  assert('没有离开编辑页', (await page.ev('location.pathname')) === '/tournament/2/edit')

  // ---- 改成 5（正好等于已报名人数）+ 改名/换场地：允许保存 ----
  page.requests = []
  assert('最大人数改成 5', (await setField('最大人数', 5)) === 'OK')
  assert('赛事改名', (await setField('赛事名称', '10月16日养生局（改）')) === 'OK')
  assert('场地号改成 B2', (await setField('场地号', 'B2')) === 'OK')
  assert('再次点「保存修改」', (await page.clickText('保存修改')) === true)
  await sleep(900)
  const saved = puts()
  assert('恰好 1 个 PUT /tournaments/2', saved.length === 1, `实际 ${saved.length}`)
  const body = saved[0]?.body || ''
  assert('请求体带上新的最大人数', /"max_participants":5/.test(body), body)
  assert('请求体带上新名称', body.includes('养生局（改）'), body)
  assert('请求体带上新场地', body.includes('"name":"B2"'), body)
  assert('编辑路径不带预选名单', /"preselect_player_ids":\[\]/.test(body), body)
  assert('提示已保存', (await toast()) === '已保存', await toast())
  assert('保存后回到详情页', (await page.ev('location.pathname')) === '/tournament/2', await page.ev('location.pathname'))
  await sleep(600)
  assert('详情页显示新名称',
    await page.ev(`(document.body.textContent || '').includes('10月16日养生局（改）')`))

  // ---- 非创建者（普通用户）：看不到入口 ----
  me = { ...me, id: 7, username: 'p7' }
  await page.goto('/tournament/2', 2200)
  assert('非创建者看不到「编辑赛事信息」',
    !(await btnTexts()).includes('编辑赛事信息'), JSON.stringify(await btnTexts()))
  assert('非创建者顶栏没有编辑图标',
    !(await navIcons()).some(c => c.includes('van-icon-edit')), JSON.stringify(await navIcons()))

  // ---- 管理员：能改别人的报名中赛事 ----
  me = { ...me, id: 9, username: 'adm', role: 'admin' }
  await page.goto('/tournament/2', 2200)
  assert('管理员能编辑别人创建的报名中赛事',
    (await btnTexts()).includes('编辑赛事信息'), JSON.stringify(await btnTexts()))

  // ---- 多场地：表单只能表达第一个，其余必须原样带回去（不能悄悄删掉） ----
  me = { ...me, id: 1, username: 'p1', role: 'user' }
  detail = {
    ...detail, creator_id: 1, status: 'open',
    courts: [
      { id: 1, name: 'A1', sort_order: 0, time_slots: [{ id: 1, start_time: '19:00:00', end_time: '21:00:00' }] },
      { id: 2, name: 'B1', sort_order: 1, time_slots: [{ id: 2, start_time: '20:00:00', end_time: '21:00:00' }] },
    ],
  }
  await page.goto('/tournament/2', 2200)
  await page.clickText('编辑赛事信息')
  await sleep(900)
  assert('多场地时表单只回填第一个场地号', (await fieldValue('场地号')) === 'A1', await fieldValue('场地号'))
  assert('改第一个场地号', (await setField('场地号', 'B9')) === 'OK')
  page.requests = []
  await page.clickText('保存修改')
  await sleep(900)
  const multi = JSON.parse(puts()[0]?.body || '{}')
  assert('第二个场地没有被吞掉（两条都发出去）', multi.courts?.length === 2,
    JSON.stringify(multi.courts?.map((x) => x.name)))
  assert('第一个场地用新名字 + 新时间段',
    multi.courts?.[0]?.name === 'B9'
    && multi.courts[0].time_slots[0].start_time === '19:00:00'
    && multi.courts[0].time_slots[0].end_time === '21:00:00',
    JSON.stringify(multi.courts?.[0]))
  assert('第二个场地原样保留（名字/次序/时间段都没动）',
    multi.courts?.[1]?.name === 'B1' && multi.courts[1].sort_order === 1
    && multi.courts[1].time_slots[0].start_time === '20:00:00'
    && multi.courts[1].time_slots[0].end_time === '21:00:00',
    JSON.stringify(multi.courts?.[1]))

  // ---- 改完人数「立刻」保存：总场次必须按新人数对齐（否则开赛时会被临时换档）----
  // 场景：8 人 / 14 场左右，报名期间把上限改成 12。档位重算挂在 @blur 上是异步的，
  // 提交若抢在它前面就会把 10 场（对 12 人不整除）原样送上去 —— 这条就是盯这个的。
  detail = {
    ...detail, creator_id: 1, status: 'open', max_participants: 8, total_matches: 10,
    courts: [{ id: 1, name: 'A1', sort_order: 0,
               time_slots: [{ id: 1, start_time: '19:00:00', end_time: '21:00:00' }] }],
  }
  await page.goto('/tournament/2', 2200)
  await page.clickText('编辑赛事信息')
  await sleep(900)
  assert('最大人数改成 12（不离开输入框就保存）', (await setField('最大人数', 12)) === 'OK')
  page.requests = []
  assert('立刻点「保存修改」', (await page.clickText('保存修改')) === true)
  await sleep(900)
  const aligned = JSON.parse(puts()[0]?.body || '{}')
  assert('PUT 里的总场次已按新人数对齐（15，而不是旧的 10）',
    aligned.max_participants === 12 && aligned.total_matches === 15,
    JSON.stringify({ max: aligned.max_participants, total: aligned.total_matches }))

  // ---- 已开赛：完全只读（超管也不行） ----
  me = { ...me, id: 1, username: 'p1', role: 'superadmin' }
  detail = { ...detail, creator_id: 1, status: 'ongoing' }
  await page.goto('/tournament/2', 2200)
  assert('已开赛（超管）：看不到「编辑赛事信息」',
    !(await btnTexts()).includes('编辑赛事信息'), JSON.stringify(await btnTexts()))
  assert('已开赛（超管）：顶栏没有编辑图标',
    !(await navIcons()).some(c => c.includes('van-icon-edit')), JSON.stringify(await navIcons()))
}

/** 场景 12：创建页（与编辑页共用同一个表单组件）的创建路径没被改坏 */
async function scenarioCreateStillWorks(page) {
  console.log('12) 创建赛事：共用表单的创建路径照旧（单场 + 上下场）')
  let posted = null
  let batchPosted = null
  await page.enableMock((path, entry) => {
    if (/auth\/me$/.test(path)) {
      return { id: 1, username: 'p1', avatar: '', gender: 'M', role: 'admin', invite_code: 'x' }
    }
    if (/tournaments\/default-title/.test(path)) return { title: '10月23日养生局' }
    if (/match-options\//.test(path)) return matchOptionsFor(Number(path.split('/').pop()))
    if (path === '/api/tournaments' && entry.method === 'POST') {
      posted = JSON.parse(entry.body || '{}')
      return { id: 9 }
    }
    if (/tournaments\/batch$/.test(path)) {
      batchPosted = JSON.parse(entry.body || '[]')
      return { ok: true, tournament_ids: [10, 11] }
    }
    if (path === '/api/tournaments') return { items: [], total: 0, has_more: false }
    if (/unread-count/.test(path)) return { count: 0 }
    if (/has-users/.test(path)) return { exists: true }
    return {}
  })
  await page.setToken()

  // ---- 单场创建 ----
  await page.goto('/create', 2400)
  assert('创建页标题栏是「创建赛事」', (await navTitleOf(page)) === '创建赛事', await navTitleOf(page))
  assert('创建页有「上下场」（编辑页才隐藏）', (await hasFieldOf(page, '上下场')) === true)
  assert('创建页管理员能看到「默认参赛人员」', (await hasFieldOf(page, '默认参赛人员')) === true)
  assert('创建页最大人数默认 8', String(await fieldValueOf(page, '最大人数')) === '8',
    String(await fieldValueOf(page, '最大人数')))
  assert('默认名按日期回填', (await fieldValueOf(page, '赛事名称')) === '10月23日养生局',
    await fieldValueOf(page, '赛事名称'))
  assert('填赛事名称', (await setFieldOf(page, '赛事名称', '10月23日养生局（e2e）')) === 'OK')
  assert('填场地号', (await setFieldOf(page, '场地号', 'C3')) === 'OK')
  assert('点「创建赛事」', (await page.clickText('创建赛事')) === true)
  await sleep(900)
  assert('发出 1 个 POST /tournaments', posted !== null, String(posted))
  assert('名称取自表单', posted?.title === '10月23日养生局（e2e）', String(posted?.title))
  assert('最大人数 8', posted?.max_participants === 8, String(posted?.max_participants))
  assert('备注留空发 null', posted?.description === null, String(posted?.description))
  assert('未开定时报名时 registration_open_at 为 null',
    posted?.registration_open_at === null && posted?.enable_scheduled_registration === false,
    JSON.stringify({ r: posted?.registration_open_at, e: posted?.enable_scheduled_registration }))
  assert('场地结构与改动前一致（sort_order 0 + 时间段取表单）',
    posted?.courts?.length === 1
    && posted.courts[0].name === 'C3' && posted.courts[0].sort_order === 0
    && posted.courts[0].time_slots[0].start_time === '19:00:00'
    && posted.courts[0].time_slots[0].end_time === '21:00:00',
    JSON.stringify(posted?.courts))
  assert('创建路径不带预选名单', Array.isArray(posted?.preselect_player_ids)
    && posted.preselect_player_ids.length === 0, JSON.stringify(posted?.preselect_player_ids))

  // ---- 创建页同样：改完人数立刻提交，总场次也得按新人数对齐 ----
  // 原先 8 人默认 14 场是合法的；人数改成 12 后 14 场不可能均分（4*14 不能被 12 整除），
  // 若不重新对齐就提交，这条赛事到开赛时会被临时换档 —— 用户看到「14 场」变别的数字。
  await page.goto('/create', 2400)
  assert('填赛事名称', (await setFieldOf(page, '赛事名称', '改完人数立刻创建')) === 'OK')
  assert('最大人数改成 12（不离开输入框就提交）', (await setFieldOf(page, '最大人数', 12)) === 'OK')
  assert('立刻点「创建赛事」', (await page.clickText('创建赛事')) === true)
  await sleep(900)
  assert('创建提交的总场次已按新人数对齐（15，而不是默认的 14）',
    posted?.max_participants === 12 && posted?.total_matches === 15,
    JSON.stringify({ max: posted?.max_participants, total: posted?.total_matches }))

  // ---- 上下场（一次建两条赛事）也走同一个 payload 构造器 ----
  await page.goto('/create', 2400)
  assert('填赛事名称', (await setFieldOf(page, '赛事名称', '10月23日养生局')) === 'OK')
  assert('填场地号', (await setFieldOf(page, '场地号', 'A9')) === 'OK')
  const switchOn = (index) => page.ev(`(() => {
    const sw = document.querySelectorAll('.van-switch')[${index}]
    if (!sw) return 'NOTFOUND'
    sw.click(); return 'OK'
  })()`)
  assert('打开「上下场」开关', (await switchOn(1)) === 'OK')
  await sleep(300)
  assert('点「创建赛事」', (await page.clickText('创建赛事')) === true)
  await sleep(900)
  assert('发出 1 个 POST /tournaments/batch', Array.isArray(batchPosted) && batchPosted.length === 2,
    String(batchPosted))
  assert('上下半场标题各自加后缀',
    batchPosted?.[0]?.title === '10月23日养生局（上半场）'
    && batchPosted?.[1]?.title === '10月23日养生局（下半场）',
    JSON.stringify(batchPosted?.map((b) => b.title)))
  assert('上下半场时间首尾相接（19:00~20:00 / 20:00~21:00）',
    batchPosted?.[0]?.courts?.[0]?.time_slots?.[0]?.start_time === '19:00:00'
    && batchPosted?.[0]?.courts?.[0]?.time_slots?.[0]?.end_time === '20:00:00'
    && batchPosted?.[1]?.courts?.[0]?.time_slots?.[0]?.start_time === '20:00:00'
    && batchPosted?.[1]?.courts?.[0]?.time_slots?.[0]?.end_time === '21:00:00',
    JSON.stringify(batchPosted?.map((b) => b.courts?.[0]?.time_slots)))
}

/** 场景 13：详情页「一次性分配裁判」对在场选手开放（管理者仍然可以） */
async function scenarioAssignRefereesAll(page) {
  console.log('13) 赛事详情：一次性分配裁判（在场选手 / 管理者 / 已退赛）')
  const players = Array.from({ length: 8 }, (_, i) => ({
    id: i + 1, user_id: i + 1, username: `p${i + 1}`, avatar: '', is_active: true,
    created_at: '2026-10-10T09:00:00',
  }))
  let me = { id: 3, username: 'p3', avatar: '', gender: 'M', role: 'user', invite_code: '' }
  let detail = {
    id: 2, creator_id: 1, title: 'T', location: 'L', status: 'ongoing', max_participants: 8,
    courts: [], registered_count: 8, cancelled_count: 0, total_matches: 14, points_to_win: 11,
    server_now: '2026-10-23T09:00:00', is_registered: true, is_on_site: true,
    started_match_count: 6, pending_referee_count: 3, created_at: '2026-10-10T09:00:00',
  }
  await page.enableMock((path) => {
    if (/auth\/me$/.test(path)) return me
    if (/auto-assign-referees$/.test(path)) {
      return { ok: true, scope: 'all', assigned: [1, 2, 3], skipped: [] }
    }
    if (/\/registrations$/.test(path)) return players
    if (/\/tournaments\/2$/.test(path)) return detail
    if (/unread-count/.test(path)) return { count: 0 }
    if (/has-users/.test(path)) return { exists: true }
    return {}
  })
  await page.setToken()

  const assignBtn = () => page.ev(`(() => {
    const b = [...document.querySelectorAll('button, .van-button')]
      .find(x => (x.textContent || '').trim().startsWith('一次性分配裁判'))
    if (!b) return null
    return { text: b.textContent.trim(), inActionBlock: !!b.closest('.action-block') }
  })()`)
  const clickAssign = () => page.ev(`(() => {
    const b = [...document.querySelectorAll('button, .van-button')]
      .find(x => (x.textContent || '').trim().startsWith('一次性分配裁判'))
    if (!b) return false
    b.click(); return true
  })()`)
  const assignPosts = () => page.requests.filter((r) => r.method === 'POST' && /auto-assign-referees$/.test(r.path))

  // ---- 普通在场选手：能看到、能点 ----
  await page.goto('/tournament/2', 2400)
  const btn = await assignBtn()
  assert('在场选手能看到「一次性分配裁判」', btn !== null, JSON.stringify(btn))
  assert('按钮上写着还剩几场', btn?.text === '一次性分配裁判（3 场）', btn?.text)
  assert('入口在选手也看得到的动作区（不是房主专属区）', btn?.inActionBlock === true, JSON.stringify(btn))

  page.requests = []
  assert('点开弹确认框', (await clickAssign()) === true)
  await sleep(500)
  const dlg = await page.dialog()
  assert('确认框标题正确', dlg?.标题 === '一次性分配裁判', dlg?.标题)
  await page.cancelDialog()
  await sleep(500)
  assert('取消后不发请求', assignPosts().length === 0, `实际 ${assignPosts().length}`)

  assert('再点一次', (await clickAssign()) === true)
  await sleep(500)
  await page.confirmDialog()
  const posts = assignPosts()
  assert('确认后恰好 1 个 auto-assign 请求', posts.length === 1, `实际 ${posts.length}`)
  assert('请求体是 scope=all（整局一次分完）', /"scope":"all"/.test(posts[0]?.body || ''), posts[0]?.body)
  assert('提示已分配场数', (await toastText(page)) === '已分配 3 场裁判', await toastText(page))

  // ---- 已退赛（不在场、也不是管理者）：入口消失 ----
  detail = { ...detail, is_registered: false, is_on_site: false }
  await page.goto('/tournament/2', 2200)
  assert('已退赛的普通选手看不到入口', (await assignBtn()) === null, JSON.stringify(await assignBtn()))

  // ---- 房主（不是选手）/ 管理员：仍然可以 ----
  detail = { ...detail, creator_id: 3, is_registered: false, is_on_site: false }
  await page.goto('/tournament/2', 2200)
  assert('房主不在场也能看到入口', (await assignBtn()) !== null, JSON.stringify(await assignBtn()))

  me = { ...me, id: 9, username: 'adm', role: 'admin' }
  detail = { ...detail, creator_id: 1, is_registered: false, is_on_site: false }
  await page.goto('/tournament/2', 2200)
  assert('管理员不在场也能看到入口', (await assignBtn()) !== null, JSON.stringify(await assignBtn()))

  // ---- 没有缺裁判的场次：谁都看不到 ----
  me = { ...me, id: 3, username: 'p3', role: 'user' }
  detail = { ...detail, creator_id: 1, is_on_site: true, pending_referee_count: 0 }
  await page.goto('/tournament/2', 2200)
  assert('pending_referee_count=0 时入口消失', (await assignBtn()) === null, JSON.stringify(await assignBtn()))
}

/** 场景 14：弱网下「慢 GET 带回旧比分」不能把刚点的分覆盖回去（点了 3 不会跳回 2） */
async function scenarioScoreNoFlicker(page) {
  console.log('14) 记分页：慢 GET 带回旧快照，不会让 3 跳回 2')
  let serverScore = 2          // 服务端当前的 a 队比分
  let slowNextGet = false      // 下一次 GET 晚 1200ms 才回（内容按发出时的 serverScore 生成）
  await page.enableMock((path, entry) => {
    if (/auth\/me$/.test(path)) {
      return { id: 1, username: '李祥', avatar: '', gender: 'M', role: 'user', invite_code: '' }
    }
    if (/\/score$/.test(path) && entry.method === 'PUT') {
      const body = JSON.parse(entry.body || '{}')
      if (body.score_a != null) serverScore = body.score_a
      return { ok: true }
    }
    if (/\/matches\/101$/.test(path)) {
      const snapshot = matchPayload({ status: 'ongoing', referee: P(1, '李祥'), active_referee: P(1, '李祥'),
                                      score_a: serverScore })
      return slowNextGet ? { ...snapshot, __delayMs: 1200 } : snapshot
    }
    if (/auth\/stats/.test(path)) return { total_matches: 0, total_wins: 0, win_rate: 0, tournaments_played: 0 }
    if (/unread-count/.test(path)) return { count: 0 }
    if (/has-users/.test(path)) return { exists: true }
    return {}
  })
  await page.setToken()
  await page.goto('/tournament/2/score/101?num=1')

  const leftScore = () => page.ev(`(document.querySelectorAll('.sb-big')[0]?.textContent || '').trim()`)
  assert('首屏显示 2 分', (await leftScore()) === '2', await leftScore())

  // 模拟一次「慢读」：先让一次 GET 发出去（读到 2，但 1200ms 后才回来），
  // 紧接着裁判点加号 —— 这正是弱网里真实发生的时序（观众点加油触发 fetchMatch 也一样）
  slowNextGet = true
  await page.ev(`(() => {
    Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'visible' })
    document.dispatchEvent(new Event('visibilitychange'))
    return true
  })()`)
  await sleep(120)
  await page.ev(`document.querySelectorAll('.wheel-btn.plus')[0].click(); true`)
  await sleep(200)
  assert('点完立刻显示 3 分（乐观更新）', (await leftScore()) === '3', await leftScore())

  // 等那份慢 GET 回来（它带的是落库前的 2 分）
  await sleep(1800)
  assert('慢 GET 回来后仍是 3 分（旧快照被丢弃）', (await leftScore()) === '3', await leftScore())
  assert('服务端确实落了 3 分', serverScore === 3, String(serverScore))

  // 反向确认闸门不会把正常更新也挡掉：服务端值更新、且本地无待提交时应当采纳
  serverScore = 5
  slowNextGet = false
  await page.ev(`(() => {
    document.dispatchEvent(new Event('visibilitychange'))
    return true
  })()`)
  await sleep(900)
  assert('本地空闲时服务端的新比分会被采纳（5）', (await leftScore()) === '5', await leftScore())
}

/** 场景 15：两台设备同时记分 —— 服务端 409 乐观锁，客户端把增量叠上去重试（不丢分） */
async function scenarioScoreOptimisticLock(page) {
  console.log('15) 记分页：两台设备同时记分不丢分（409 + 增量重试）')
  let server = { score_a: 2, score_b: 0, score_version: 7 }
  await page.enableMock((path, entry) => {
    if (/auth\/me$/.test(path)) {
      return { id: 1, username: '李祥', avatar: '', gender: 'M', role: 'user', invite_code: '' }
    }
    if (/\/score$/.test(path) && entry.method === 'PUT') {
      const body = JSON.parse(entry.body || '{}')
      if (body.expected_version != null && body.expected_version !== server.score_version) {
        // 基线过期：别人先记过这一场
        return { __status: 409, __body: { detail: { message: '比分已被其他人更新，请重试',
                                                   ...server } } }
      }
      server = { score_a: body.score_a, score_b: body.score_b, score_version: server.score_version + 1 }
      return { ok: true, score_version: server.score_version }
    }
    if (/\/matches\/101$/.test(path)) {
      return matchPayload({ status: 'ongoing', referee: P(1, '李祥'), active_referee: P(1, '李祥'),
                            score_a: server.score_a, score_b: server.score_b,
                            score_version: server.score_version })
    }
    if (/auth\/stats/.test(path)) return { total_matches: 0, total_wins: 0, win_rate: 0, tournaments_played: 0 }
    if (/unread-count/.test(path)) return { count: 0 }
    if (/has-users/.test(path)) return { exists: true }
    return {}
  })
  await page.setToken()
  await page.goto('/tournament/2/score/101?num=1')

  const leftScore = () => page.ev(`(document.querySelectorAll('.sb-big')[0]?.textContent || '').trim()`)
  assert('首屏 2 分、基线版本 7', (await leftScore()) === '2', await leftScore())

  // 模拟「另一台设备先记了一分」：服务端变成 3 分 / 版本 8，而本页基线仍是 7
  server = { score_a: 3, score_b: 0, score_version: 8 }
  page.requests = []
  await page.ev(`document.querySelectorAll('.wheel-btn.plus')[0].click(); true`)
  await sleep(1200)
  const puts = scorePuts(page)
  assert('发生了重试（2 个记分请求）', puts.length === 2, `实际 ${puts.length}`)
  const first = JSON.parse(puts[0]?.body || '{}')
  const second = JSON.parse(puts[1]?.body || '{}')
  assert('第一次带的是本地基线版本 7', first.expected_version === 7, JSON.stringify(first))
  assert('第一次提交的是我以为的 3 分', first.score_a === 3, JSON.stringify(first))
  assert('409 后带上服务端新版本 8 重试', second.expected_version === 8, JSON.stringify(second))
  assert('重试值是「服务端 3 + 我的这一分」= 4（两分都不丢）',
    second.score_a === 4, JSON.stringify(second))
  assert('界面上也是 4 分', (await leftScore()) === '4', await leftScore())
  assert('服务端最终 4 分 / 版本 9',
    server.score_a === 4 && server.score_version === 9, JSON.stringify(server))

  // 基线已经更新到 9：再点一次要带 9，不应该再触发一次 409
  page.requests = []
  await page.ev(`document.querySelectorAll('.wheel-btn.plus')[0].click(); true`)
  await sleep(900)
  const puts2 = scorePuts(page)
  assert('第二次点击只发 1 个请求（基线已同步）', puts2.length === 1, `实际 ${puts2.length}`)
  assert('带的是最新版本 9', JSON.parse(puts2[0]?.body || '{}').expected_version === 9, puts2[0]?.body)
  assert('最终 5 分', (await leftScore()) === '5', await leftScore())
}

/** 场景 16：访问控制 —— 未登录访问需要登录的页面（以积分榜为例）会被带去登录页并记住原目标 */
async function scenarioAuthGuard(page) {
  console.log('16) 访问控制：未登录访问积分榜 → 登录页；登录后正常打开')
  await page.enableMock((path) => {
    if (/auth\/me$/.test(path)) {
      return { id: 1, username: '李祥', avatar: '', gender: 'M', role: 'user', invite_code: '' }
    }
    if (/\/rankings$/.test(path)) return { tournament_id: 2, tournament_title: 'T', rankings: [] }
    if (/unread-count/.test(path)) return { count: 0 }
    if (/has-users/.test(path)) return { exists: true }
    return {}
  })
  // 先随便落地一次才能操作 localStorage；清完就是"未登录"状态
  await page.goto('/', 1600)
  await page.ev(`localStorage.clear(); sessionStorage.clear(); true`)
  await page.goto('/tournament/2/rankings', 1800)
  assert('未登录访问积分榜 → 跳登录页',
    (await page.ev('location.pathname')) === '/profile', await page.ev('location.pathname'))
  assert('记下了原目标（登录后能回跳）',
    (await page.ev(`sessionStorage.getItem('loginRedirect')`)) === '/tournament/2/rankings',
    String(await page.ev(`sessionStorage.getItem('loginRedirect')`)))

  // 带上 token 再访问：正常渲染（积分榜不再匿名，但登录用户一切照旧）
  await page.ev(`localStorage.setItem('token','mock'); true`)
  await page.goto('/tournament/2/rankings', 2200)
  assert('登录后能正常打开积分榜',
    (await page.ev('location.pathname')) === '/tournament/2/rankings', await page.ev('location.pathname'))
  const title = await page.ev(`(document.querySelector('.van-nav-bar__title')?.textContent || '').trim()`)
  assert('页面标题是「积分榜」', title === '积分榜', title)
}

// ---------------------------------------------------------------- 运行
const scenarios = [scenarioRefereeScoring, scenarioFixFlow, scenarioMatchRefereeCanFix, scenarioLayout, scenarioInviteLabel, scenarioScheduleFilter, scenarioFilterWithFinished, scenarioScheduleOnlyMode, scenarioScheduleRefereeRow, scenarioAddMatches, scenarioAdminUserActions, scenarioEditTournament, scenarioCreateStillWorks, scenarioAssignRefereesAll, scenarioScoreNoFlicker, scenarioScoreOptimisticLock, scenarioAuthGuard]

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
