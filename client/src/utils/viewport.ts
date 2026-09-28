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
 * 实测「贴底 fixed 元素」与「可见区底边」的差值。
 *
 * 为什么用探针实测而不是拿 innerHeight 猜：不同内核把 fixed 的 bottom 锚在不同的边上。
 *   - 锚在可见底边（Chromium 常见）：探针贴住可见底边 → 差值 0 → 不影响任何布局
 *   - 锚在布局视口底边（带底部工具条的浏览器）：探针落在屏幕外 → 差值即被切掉的高度
 * 底部弹层「最后一行被切一半且滚不到」就是后者造成的。
 */
function measureBrowserGap(visibleHeight: number): number {
  const probe = document.createElement('div')
  probe.style.cssText =
    'position:fixed;bottom:0;left:0;width:1px;height:1px;pointer-events:none;visibility:hidden'
  document.body.appendChild(probe)
  const bottom = probe.getBoundingClientRect().bottom
  probe.remove()

  // 键盘弹出时 visualViewport 会大幅缩小，那不是浏览器工具栏：
  // 不设上限的话弹层会被顶到屏幕中间。超过上限就认为当前是键盘态，不做偏移。
  const gap = bottom - visibleHeight
  if (!Number.isFinite(gap) || gap <= 0.5 || gap > MAX_BROWSER_GAP) return 0
  return Math.round(gap)
}

export function syncViewportVars(): void {
  // 用 visualViewport：它才是「用户真正能看到的高度」，
  // 地址栏收起/展开只触发它的 resize，不一定触发 window 的 resize
  const visibleHeight = window.visualViewport?.height ?? window.innerHeight
  const root = document.documentElement.style
  root.setProperty('--vh', `${visibleHeight / 100}px`)
  root.setProperty('--browser-gap', `${measureBrowserGap(visibleHeight)}px`)
}

export function installViewportSync(): void {
  syncViewportVars()
  window.addEventListener('resize', syncViewportVars)
  window.addEventListener('orientationchange', syncViewportVars)
  window.visualViewport?.addEventListener('resize', syncViewportVars)
}
