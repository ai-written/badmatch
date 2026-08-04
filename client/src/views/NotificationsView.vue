<template>
  <div class="notify-page">
    <van-nav-bar title="站内消息" left-text="返回" left-arrow @click-left="goBack">
      <template #right>
        <span v-if="hasUnread" class="read-all" @click="markAllRead">全部已读</span>
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
          </van-cell-group>
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
import { showToast } from 'vant'

const router = useRouter()
const { goBack } = useGoBack()
const notifications = ref<any[]>([])
const refreshing = ref(false)

const hasUnread = computed(() => notifications.value.some(n => !n.is_read))

function formatTime(t: string) {
  if (!t) return ''
  return t.replace('T', ' ').slice(0, 16)
}

async function fetchList() {
  const res = await api.get('/notifications')
  notifications.value = res.data
}

async function onRefresh() {
  try { await fetchList() } finally { refreshing.value = false }
}

async function openNotification(n: any) {
  if (!n.is_read) {
    try {
      await api.post(`/notifications/${n.id}/read`)
      n.is_read = true
    } catch {}
  }
  if (n.tournament_id) router.push(`/tournament/${n.tournament_id}`)
}

async function markAllRead() {
  try {
    await api.post('/notifications/read-all')
    notifications.value.forEach(n => { n.is_read = true })
    showToast('已全部标记为已读')
  } catch {}
}

onMounted(fetchList)
</script>

<style scoped>
.notify-page { height: 100vh; display: flex; flex-direction: column; background: #f5f6f8; }
.notify-scroll { flex: 1; overflow-y: auto; }
.pull-fill { min-height: 100%; }
.pull-inner { padding-bottom: 60px; }
.read-all { color: #1989fa; font-size: 14px; }
.dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: #ee0a24; margin-right: 10px; flex-shrink: 0; }
.dot.read { background: #dcdee0; }
</style>
