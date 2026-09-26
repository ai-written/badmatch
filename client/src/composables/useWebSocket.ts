import { ref, onMounted, onUnmounted } from 'vue'

// 连接建立超时：SYN 被静默丢弃时 TCP 握手会一直挂起，
// WebSocket 停在 CONNECTING 且既不触发 onopen 也不触发 onclose，
// 此时若不主动兜底，实时更新会永久静默失效。
const CONNECT_TIMEOUT_MS = 10000
// 断线重连间隔
const RECONNECT_DELAY_MS = 3000
// 应用层保活心跳间隔
const HEARTBEAT_MS = 30000
// 存活判定：连续两个心跳周期都没收到任何消息，就认为连接已死。
// 覆盖两种「没有事件」的死法：
//   1) 连接被静默终结（页面被 bfcache 冻结、后台标签被系统回收），
//      close 事件不保证补发，readyState 已是 CLOSED 却没有任何回调；
//   2) 半开连接（NAT/代理回收映射、Wi-Fi 切蜂窝），readyState 仍是 OPEN，
//      数据写进黑洞，要等 TCP 重传耗尽（分钟级）才可能有 onclose。
// 服务端收到应用层 ping 会回 pong，因此「静默」本身就是死亡信号。
const ALIVE_TIMEOUT_MS = HEARTBEAT_MS * 2 + 5000

export function useWebSocket(tournamentId: number | null) {
  const ws = ref<WebSocket | null>(null)
  const lastMessage = ref<any>(null)
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null
  let connectTimer: ReturnType<typeof setTimeout> | null = null
  let heartbeatTimer: ReturnType<typeof setInterval> | null = null
  let lastAliveAt = 0
  let disposed = false

  function clearConnectTimer() {
    if (connectTimer) { clearTimeout(connectTimer); connectTimer = null }
  }

  function closeCurrent() {
    const old = ws.value
    ws.value = null
    if (!old) return
    old.onopen = null
    old.onmessage = null
    old.onclose = null
    old.onerror = null
    try {
      if (old.readyState === WebSocket.CONNECTING || old.readyState === WebSocket.OPEN) {
        old.close()
      }
    } catch {}
  }

  function scheduleReconnect() {
    if (disposed) return
    if (reconnectTimer) clearTimeout(reconnectTimer)
    // 触发后置空：下面的心跳兜底靠它判断「是否已有待重连」，留着旧句柄会判断失真
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null
      connect()
    }, RECONNECT_DELAY_MS)
  }

  function connect() {
    if (!tournamentId || disposed) return
    // 已有一个正在连接或已连接的实例时不要重复创建
    if (ws.value && (ws.value.readyState === WebSocket.CONNECTING || ws.value.readyState === WebSocket.OPEN)) {
      return
    }
    closeCurrent()

    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
    // localStorage 在隐私模式/禁用存储时会抛异常；new WebSocket 也可能抛。
    // 抛在这里若不接住，调用方 onMounted 会中断（连心跳定时器都建不起来），
    // 且不会进入任何事件回调 -> 永远不会重连。
    let socket: WebSocket
    try {
      const token = localStorage.getItem('token') || ''
      const url = `${protocol}//${location.host}/ws/tournaments/${tournamentId}?token=${encodeURIComponent(token)}`
      socket = new WebSocket(url)
    } catch {
      scheduleReconnect()
      return
    }
    ws.value = socket

    // 超时仍未连上就换掉这个实例并重连（覆盖 CONNECTING 卡死的情况）
    clearConnectTimer()
    connectTimer = setTimeout(() => {
      connectTimer = null
      if (disposed) return
      if (ws.value === socket && socket.readyState === WebSocket.CONNECTING) {
        closeCurrent()
        scheduleReconnect()
      }
    }, CONNECT_TIMEOUT_MS)

    socket.onopen = () => {
      clearConnectTimer()
      lastAliveAt = Date.now()
    }

    socket.onmessage = (ev) => {
      lastAliveAt = Date.now()
      let data: any
      try {
        data = JSON.parse(ev.data)
      } catch {
        return
      }
      // 心跳回包不进业务流：它每 30s 就来一次，
      // 而 TournamentDetail 的 watcher 没有类型过滤，会因此每 30s 空拉两次接口
      if (data?.type === 'pong') return
      lastMessage.value = data
    }

    socket.onerror = () => {
      // 连接失败/异常：立即清理并在稍后重连。
      // 某些失败场景（如握手被中断）不会触发 onclose，必须有这支兜底。
      if (disposed || ws.value !== socket) return
      clearConnectTimer()
      closeCurrent()
      scheduleReconnect()
    }

    socket.onclose = (ev) => {
      if (disposed || ws.value !== socket) return
      clearConnectTimer()
      ws.value = null
      // 4401 = 未授权（token 失效/被踢），等重新登录，不自动重连
      if (ev.code === 4401) return
      scheduleReconnect()
    }
  }

  function sendHeartbeat() {
    if (disposed) return
    const socket = ws.value
    if (!socket) {
      // 正常路径 onclose 已安排重连，这里只做幂等兜底
      if (!reconnectTimer) scheduleReconnect()
      return
    }
    if (socket.readyState === WebSocket.CLOSED || socket.readyState === WebSocket.CLOSING) {
      // 被静默终结（bfcache/系统回收）：不会再有事件，必须自己重建
      closeCurrent()
      scheduleReconnect()
      return
    }
    if (socket.readyState === WebSocket.OPEN) {
      if (lastAliveAt && Date.now() - lastAliveAt > ALIVE_TIMEOUT_MS) {
        // 半开连接：写进去也没人应答
        closeCurrent()
        scheduleReconnect()
        return
      }
      try {
        socket.send('ping')
      } catch {
        closeCurrent()
        scheduleReconnect()
      }
    }
    // CONNECTING：交给连接超时兜底，这里不动
  }

  onMounted(() => {
    connect()
    heartbeatTimer = setInterval(sendHeartbeat, HEARTBEAT_MS)
  })

  onUnmounted(() => {
    disposed = true
    if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null }
    clearConnectTimer()
    if (heartbeatTimer) { clearInterval(heartbeatTimer); heartbeatTimer = null }
    closeCurrent()
  })

  return { lastMessage }
}
