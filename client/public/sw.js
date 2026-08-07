/*
 * Service Worker - 低带宽移动端优化
 * - /assets/*（带 hash 的构建产物）: 缓存优先，配合 nginx 30 天 immutable 缓存
 * - 页面导航: 网络优先，失败时回退缓存的 index.html（离线兜底）
 * - /api、/ws、/static 不拦截，保证实时性与数据正确
 */
const CACHE_NAME = 'badmatch-v1'
const MAX_CACHE_ENTRIES = 100

self.addEventListener('install', () => {
  self.skipWaiting()
})

self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    const keys = await caches.keys()
    await Promise.all(keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key)))
    await self.clients.claim()
  })())
})

self.addEventListener('fetch', (event) => {
  const request = event.request
  if (request.method !== 'GET') return

  const url = new URL(request.url)
  if (url.origin !== self.location.origin) return
  if (url.pathname.startsWith('/api/') || url.pathname.startsWith('/ws/') || url.pathname.startsWith('/static/')) return

  if (url.pathname.startsWith('/assets/')) {
    event.respondWith(
      caches.match(request).then((cached) => {
        if (cached) return cached
        return fetch(request).then((response) => {
          if (response.ok) cacheResponse(request, response)
          return response
        })
      }),
    )
    return
  }

  if (request.mode === 'navigate') {
    event.respondWith(
      fetch(request)
        .then((response) => {
          if (response.ok) cacheResponse('/index.html', response)
          return response
        })
        .catch(() => caches.match('/index.html')),
    )
  }
})

function cacheResponse(request, response) {
  const copy = response.clone()
  caches.open(CACHE_NAME).then((cache) => {
    cache.put(request, copy)
    // 控制缓存上限，避免多次发版后旧 hash 资源无限累积
    cache.keys().then((keys) => {
      if (keys.length > MAX_CACHE_ENTRIES) {
        const overflow = keys.slice(0, keys.length - MAX_CACHE_ENTRIES)
        Promise.all(overflow.map((key) => cache.delete(key)))
      }
    })
  })
}
