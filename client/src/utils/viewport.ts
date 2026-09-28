/**
 * 移动端视口高度同步。
 *
 * 问题：移动浏览器的 100vh 取的是「地址栏收起时的大视口」。地址栏一展开，
 * 100vh 就比真正可见的区域高出几十像素；而 main.css 里
 * `html, body { overflow: hidden }` 又关掉了整页滚动，于是多出来的部分
 * 既看不见也滚不回来。实测（真实 Chromium 内核 + 假后端）：
 *   - 我的页：地址栏 56px 时「退出登录」被 tabbar 压住 26px
 *   - 首页：地址栏只要超过 10px 就开始被压
 *   - 任何可见区外的内容，window.scrollTo 都救不回来（scrollY 恒为 0）
 *
 * 对策：把「真实可见高度」交给 CSS 变量，页面与弹层高度不再直接依赖 vh：
 *   --vh           可见高度的 1%（写法见 main.css 的 .vh-page / .vh-*）
 *   --browser-gap  贴底 fixed 元素离可见底边还差多少
 */

/** 浏览器工具栏（含底部工具条）一般不超过这个高度；键盘会缩几百像素，属于另一回事 */
const MAX_BROWSER_GAP = 160

/**
 * 是否需要「贴底 fixed 补偿」。
 *
 * iOS/WebKit（iPhone 上所有浏览器都是 WebKit，微信内置也是）**一律不补**：
 * WebKit 的 fixed 元素本来就贴着「可见底边」、并随视觉视口一起移动，
 * 这条补偿对它毫无意义；反而会因为它自己算出的差值而把 tabbar 抬起来、
 * 在下方留一条空白（线上实测：innerH/vv.h 都是 670，探针却报 83，
 * 于是 tabbar 落在 537~587、下面留 83px）。
 *
 * 真正需要补偿的是 Blink/X5 一类内核——它们把 fixed 锚在「布局视口底边」，
 * 于是带底部工具条的浏览器里弹层最后一行会被切掉。
 */
function needsBottomGapCompensation(): boolean {
  // 'Apple Computer, Inc.' 是 iOS/macOS 上 WebKit 的固定取值；
  // 桌面 Safari 判成 true 也无妨（同样不需要补偿）
  return navigator.vendor !== 'Apple Computer, Inc.'
}

/**
 * 实测「贴底 fixed 元素」与「可见区底边」的差值。
 *
 * 为什么用探针实测而不是拿 innerHeight 猜：不同内核把 fixed 的 bottom 锚在不同的边上。
 *   - 锚在可见底边（Chromium 常见）：探针贴住可见底边 → 差值 0 → 不影响任何布局
 *   - 锚在布局视口底边（带底部工具条的浏览器）：探针落在屏幕外 → 差值即被切掉的高度
 * 底部弹层「最后一行被切一半且滚不到」就是后者造成的。
 */
function measureBrowserGap(visibleHeight: number): number {
  if (!needsBottomGapCompensation()) return 0

  const probe = document.createElement('div')
  probe.style.cssText =
    'position:fixed;bottom:0;left:0;width:1px;height:1px;pointer-events:none;visibility:hidden'
  // body 通常已存在（模块脚本在文档解析完成后才执行）；退一步挂到 documentElement，
  // 这样调用时机不受「先 mount 还是先同步」的顺序影响
  const host = document.body || document.documentElement
  host.appendChild(probe)
  const bottom = probe.getBoundingClientRect().bottom
  probe.remove()

  // 键盘弹出时 visualViewport 会大幅缩小，那不是浏览器工具栏：
  // 不设上限的话弹层会被顶到屏幕中间。超过上限就认为当前是键盘态，不做偏移。
  const gap = bottom - visibleHeight
  if (!Number.isFinite(gap) || gap <= 0.5 || gap > MAX_BROWSER_GAP) return 0
  return Math.round(gap)
}

let lastVh = ''
let lastGap = ''

export function syncViewportVars(): void {
  // --vh 用 innerHeight（布局视口高度），**不能**用 visualViewport.height：
  // iOS 上键盘弹出只缩 visualViewport、不缩布局视口，跟着 visualViewport 走的话
  // 键盘收起后这个值会停在键盘态——线上实测：整页只剩屏幕的 60%，
  // 而 844 - 336(微信键盘高) ≈ 508，正好就是页面底部所在的位置，
  // 表现为固定 tabbar 被钉在半空、下方一大片空白。Android 上两者本来就一起变，
  // 所以改用 innerHeight 没有任何损失（键盘态下页面跟着缩，反而符合 Android 预期）。
  //
  // --browser-gap 仍要用 visualViewport.height：它代表「可见底边」，
  // 而探针量的是「布局把 fixed 元素放到了哪条边上」，两者相减才是真正的差值。
  //
  // 注意：高度只留这一个来源。main.css 里刻意不再写 100dvh —— 那条会静默覆盖
  // --vh，让下面所有同步逻辑（innerHeight、pageshow、触摸时重算）统统失效。
  const visibleBottom = window.visualViewport?.height ?? window.innerHeight
  const pageHeight = window.innerHeight
  const root = document.documentElement.style
  const vh = `${pageHeight / 100}px`
  const gap = `${measureBrowserGap(visibleBottom)}px`
  // 只在真的变了才写：写 CSS 变量会让整页样式失效重算，而交互监听触发很频繁
  if (vh !== lastVh) {
    root.setProperty('--vh', vh)
    lastVh = vh
  }
  if (gap !== lastGap) {
    root.setProperty('--browser-gap', gap)
    lastGap = gap
  }
}

let lastSyncAt = 0

/** 交互触发的同步：键盘收起后 iOS 的视口值可能晚一拍才稳定，用户下一次触摸时对一次账 */
function syncOnInteract(): void {
  const now = Date.now()
  if (now - lastSyncAt < 500) return
  lastSyncAt = now
  syncViewportVars()
}

export function installViewportSync(): void {
  syncViewportVars()
  window.addEventListener('resize', syncViewportVars)
  window.addEventListener('orientationchange', syncViewportVars)
  // 地址栏/键盘/toolbar 的变化不一定触发 window 的 resize；iOS 上尤其如此
  window.visualViewport?.addEventListener('resize', syncViewportVars)
  window.visualViewport?.addEventListener('scroll', syncOnInteract)
  // bfcache 恢复、从后台切回前台时视口可能已经变了
  window.addEventListener('pageshow', syncViewportVars)
  document.addEventListener('visibilitychange', syncViewportVars)
  // 兜底：键盘收起后若 resize 没来（iOS 偶发），用户一触摸就自动纠正
  window.addEventListener('touchstart', syncOnInteract, { passive: true })
  window.addEventListener('focusout', () => setTimeout(syncViewportVars, 300))
  // 内层滚动（页面自己滚，文档不滚）也用捕获阶段跟一下：微信工具栏的显隐常与滚动相关
  document.addEventListener('scroll', syncOnInteract, { capture: true, passive: true })
  // 微信内置浏览器的底部工具栏会自己显隐，而且**不保证发任何事件**
  // （线上实测：同一页 innerHeight 时而 753、时而 670，中间没有任何 resize）。
  // 所以再挂一个低频心跳兜底——只读一次 rect，且值没变就不写 CSS 变量，开销可忽略。
  setInterval(() => {
    if (document.visibilityState === 'visible') syncViewportVars()
  }, 1000)
}
