import { onMounted, onUnmounted } from 'vue'

/**
 * 回到前台时补一次刷新。
 *
 * 手机/电脑锁屏或切到后台后，浏览器会冻结页面定时器，WebSocket 也可能被系统
 * 断开，解锁回来时页面上的数据可能已是旧的（比分、状态、耗时等）。
 * 这里监听 visibilitychange 与 pageshow(bfcache)，回到前台时主动拉取一次。
 *
 * @param refresh 刷新回调（通常是静默拉取）
 * @param minIntervalMs 两次自动刷新之间的最小间隔，避免频繁前后台切换造成请求风暴
 */
export function useResumeRefresh(refresh: () => void | Promise<void>, minIntervalMs = 2000) {
  let lastAt = 0
  let hiddenAt = 0
  let running = false

  async function run() {
    if (document.visibilityState !== 'visible' || running) return
    if (Date.now() - lastAt < minIntervalMs) return
    running = true
    lastAt = Date.now()
    try {
      await refresh()
    } catch {
      // 网络不可用时保持现状：WebSocket 重连或下次切换回来时会再试
    } finally {
      running = false
    }
  }

  function onVisibilityChange() {
    if (document.visibilityState === 'visible') {
      // 只有在后台确实待够 minIntervalMs，才允许回来立刻刷新；
      // 否则沿用最小间隔。若在这里无条件清零 lastAt，防抖就形同虚设——
      // 反复下拉通知栏、来回切应用会每次都打一整包请求。
      if (hiddenAt && Date.now() - hiddenAt >= minIntervalMs) lastAt = 0
      hiddenAt = 0
      run()
    } else {
      hiddenAt = Date.now()
    }
  }

  function onPageShow(e: PageTransitionEvent) {
    // bfcache 恢复：整页被冻结过，必须重新拉取
    if (e.persisted) run()
  }

  onMounted(() => {
    lastAt = Date.now()
    document.addEventListener('visibilitychange', onVisibilityChange)
    window.addEventListener('pageshow', onPageShow)
  })
  onUnmounted(() => {
    document.removeEventListener('visibilitychange', onVisibilityChange)
    window.removeEventListener('pageshow', onPageShow)
  })

  return { refreshOnResume: run }
}
