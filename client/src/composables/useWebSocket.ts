import { ref, onMounted, onUnmounted } from 'vue'

export function useWebSocket(tournamentId: number | null) {
  const ws = ref<WebSocket | null>(null)
  const lastMessage = ref<any>(null)
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null
  let heartbeatTimer: ReturnType<typeof setInterval> | null = null
  let disposed = false

  function connect() {
    if (!tournamentId || disposed) return
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
    const url = `${protocol}//${location.host}/ws/tournaments/${tournamentId}`
    ws.value = new WebSocket(url)
    ws.value.onmessage = (ev) => {
      lastMessage.value = JSON.parse(ev.data)
    }
    ws.value.onclose = () => {
      if (disposed) return
      // auto-reconnect after 3s
      if (reconnectTimer) clearTimeout(reconnectTimer)
      reconnectTimer = setTimeout(connect, 3000)
    }
  }

  function sendHeartbeat() {
    if (ws.value?.readyState === WebSocket.OPEN) {
      ws.value.send('ping')
    }
  }

  onMounted(() => {
    connect()
    heartbeatTimer = setInterval(sendHeartbeat, 30000)
  })

  onUnmounted(() => {
    disposed = true
    if (reconnectTimer) clearTimeout(reconnectTimer)
    if (heartbeatTimer) clearInterval(heartbeatTimer)
    ws.value?.close()
    ws.value = null
  })

  return { lastMessage }
}
