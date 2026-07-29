import { ref, onMounted, onUnmounted } from 'vue'

export function useWebSocket(tournamentId: number | null) {
  const ws = ref<WebSocket | null>(null)
  const lastMessage = ref<any>(null)

  function connect() {
    if (!tournamentId) return
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
    const url = `${protocol}//${location.host}/ws/tournaments/${tournamentId}`
    ws.value = new WebSocket(url)
    ws.value.onmessage = (ev) => {
      lastMessage.value = JSON.parse(ev.data)
    }
    ws.value.onclose = () => {
      // auto-reconnect after 3s
      setTimeout(connect, 3000)
    }
  }

  function sendHeartbeat() {
    if (ws.value?.readyState === WebSocket.OPEN) {
      ws.value.send('ping')
    }
  }

  onMounted(() => {
    connect()
    const interval = setInterval(sendHeartbeat, 30000)
    onUnmounted(() => clearInterval(interval))
  })

  onUnmounted(() => {
    ws.value?.close()
  })

  return { lastMessage }
}
