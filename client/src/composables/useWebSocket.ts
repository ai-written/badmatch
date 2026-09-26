import { ref, onMounted, onUnmounted } from 'vue'

// 连接建立超时：SYN 被静默丢弃时 TCP 握手会一直挂起，
// WebSocket 停在 CONNECTING 且既不触发 onopen 也不触发 onclose，
// 此时若不主动兜底，实时更新会永久静默失效。
const CONNECT_TIMEOUT_MS = 10000
// 断线重连间隔
const RECONNECT_DELAY_MS = 3000
// 应用层保活心跳间隔
const HEARTBEAT_MS = 30000

export function useWebSocket(tournamentId: number | null) {
  const ws = ref<WebSocket | null>(null)
  const lastMessage = ref<any>(null)
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null
  let connectTimer: ReturnType<typeof setTimeout> | null = null
  let heartbeatTimer: ReturnType<typeof setInterval> | null = null
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
    reconnectTimer = setTimeout(connect, RECONNECT_DELAY_MS)
  }

  function connect() {
    if (!tournamentId || disposed) return
    // 已有一个正在连接或已连接的实例时不要重复创建
    if (ws.value && (ws.value.readyState === WebSocket.CONNECTING || ws.value.readyState === WebSocket.OPEN)) {
      return
    }
    closeCurrent()

    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
    const token = localStorage.getItem('token') || ''
    const url = `${protocol}//${location.host}/ws/tournaments/${tournamentId}?token=${encodeURIComponent(token)}`
    const socket = new WebSocket(url)
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
    }

    socket.onmessage = (ev) => {
      lastMessage.value = JSON.parse(ev.data)
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
    if (ws.value?.readyState === WebSocket.OPEN) {
      ws.value.send('ping')
    }
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
