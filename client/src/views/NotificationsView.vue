<template>
  <div class="notify-page vh-page">
    <van-nav-bar title="站内消息" left-text="返回" left-arrow @click-left="goBack">
      <template #right>
        <span v-if="showReadAll" class="read-all" @click="markAllRead">全部已读</span>
      </template>
    </van-nav-bar>

    <div class="notify-scroll">
      <van-pull-refresh v-model="refreshing" @refresh="onRefresh" class="pull-fill">
        <div class="pull-inner">
          <van-cell-group inset v-if="notifications.length">
            <van-cell
              v-for="n in notifications"
              :key="n.id"
              clickable
              :title="n.message"
              :label="formatTime(n.created_at)"
              @click="openNotification(n)"
            >
              <template #icon>
                <span class="dot" :class="{ read: n.is_read }"></span>
              </template>
            </van-cell>
            <div v-if="hasMore" class="more-hint">
              仅显示最近 100 条{{ unreadCount > 0 ? `，另有未读 ${unreadCount} 条` : '' }}
            </div>
          </van-cell-group>
          <!-- 失败态与「暂无消息」必须区分：接口报错时不能说没有消息 -->
          <div v-else-if="loadFailed" class="notify-failed">
            <van-icon name="warning-o" size="22" />
            <span>消息加载失败</span>
            <van-button size="small" round plain type="primary" @click="fetchList()">重试</van-button>
          </div>
          <van-empty v-else description="暂无消息" />
        </div>
      </van-pull-refresh>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import api from '@/api/client'
import { useGoBack } from '@/composables/useGoBack'
import { useResumeRefresh } from '@/composables/useResumeRefresh'
import { showToast } from 'vant'

const router = useRouter()
const { goBack } = useGoBack()
const notifications = ref<any[]>([])
const refreshing = ref(false)
const loadFailed = ref(false)
// 未读数用服务端返回的**全表**统计，不能靠列表里有没有未读项来判断：
// 列表最多 100 条，未读若都在更早的记录里，按钮会消失、那些未读永远清不掉
const unreadCount = ref(0)
const hasMore = ref(false)

// 「全部已读」按钮的显示条件用全量未读数，而不是列表内的未读项
const showReadAll = computed(() => unreadCount.value > 0)

function formatTime(t: string) {
  if (!t) return ''
  return t.replace('T', ' ').slice(0, 16)
}

async function fetchList(skipLoading = false) {
  try {
    const res = await api.get('/notifications', { skipLoading } as any)
    const data = res.data || {}
    notifications.value = Array.isArray(data.items) ? data.items : []
    unreadCount.value = data.unread_count || 0
    hasMore.value = !!data.has_more
    loadFailed.value = false
  } catch (e) {
    loadFailed.value = true
    throw e
  }
}

async function onRefresh() {
  try { await fetchList(true) } catch { /* 拦截器已提示 */ } finally { refreshing.value = false }
}

async function openNotification(n: any) {
  if (!n.is_read) {
    try {
      await api.post(`/notifications/${n.id}/read`)
      n.is_read = true
      if (unreadCount.value > 0) unreadCount.value -= 1
    } catch {}
  }
  if (n.tournament_id) router.push(`/tournament/${n.tournament_id}`)
}

async function markAllRead() {
  try {
    await api.post('/notifications/read-all')
    notifications.value.forEach(n => { n.is_read = true })
    unreadCount.value = 0
    showToast('已全部标记为已读')
  } catch {}
}

// 本页没有 WebSocket：锁屏/切后台回来时补拉一次，否则新消息不会出现
useResumeRefresh(() => fetchList(true))

onMounted(async () => {
  try {
    await fetchList()
  } catch {
    // fetchList 已把 loadFailed 置位，界面显示失败态
  }
})
</script>

<style scoped>
.notify-page { display: flex; flex-direction: column; background: #f5f6f8; }
.notify-scroll { flex: 1; overflow-y: auto; }
.pull-fill { min-height: 100%; }
.pull-inner { padding-bottom: 60px; }
.read-all { color: #1989fa; font-size: 14px; }
.dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: #ee0a24; margin-right: 10px; flex-shrink: 0; }
.dot.read { background: #dcdee0; }
.more-hint { text-align: center; color: #969799; font-size: 12px; padding: 10px 0; }
.notify-failed {
  display: flex; flex-direction: column; align-items: center; gap: 8px;
  padding: 80px 0; color: #969799; font-size: 13px;
}
</style>
