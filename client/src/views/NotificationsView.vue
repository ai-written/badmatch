<template>
  <div class="notify-page ui-page vh-page">
    <div class="notify-scroll ui-scroll">
      <van-pull-refresh v-model="refreshing" @refresh="onRefresh" class="pull-fill">
        <div class="pull-inner">
          <AppHero :height="132">
            <div class="ui-hero-nav">
              <button class="back" @click="goBack">‹ 返回</button>
              <span class="title">站内消息</span>
              <span class="acts">
                <span v-if="showReadAll" class="read-all" @click="markAllRead">全部已读</span>
              </span>
            </div>
            <div class="hero-sub">{{ unreadCount > 0 ? `${unreadCount} 条未读` : '暂无未读' }}</div>
          </AppHero>

          <div class="ui-sheet">
            <template v-if="notifications.length">
              <div
                v-for="n in notifications"
                :key="n.id"
                class="ui-card msg"
                :class="{ unread: !n.is_read }"
                @click="openNotification(n)"
              >
                <div class="msg-ic"><van-icon name="volume-o" size="17" /></div>
                <div class="msg-bd">
                  <div class="msg-title">
                    <span class="msg-text">{{ n.message }}</span>
                    <span v-if="!n.is_read" class="dot"></span>
                  </div>
                  <div class="msg-time ui-num">{{ formatTime(n.created_at) }}</div>
                </div>
                <van-icon name="arrow" class="msg-go" size="14" />
              </div>
              <div v-if="hasMore" class="more-hint">
                仅显示最近 100 条{{ unreadCount > 0 ? `，另有未读 ${unreadCount} 条` : '' }}
              </div>
            </template>
            <!-- 失败态与「暂无消息」必须区分：接口报错时不能说没有消息 -->
            <div v-else-if="loadFailed" class="notify-failed">
              <van-icon name="warning-o" size="22" />
              <span>消息加载失败</span>
              <van-button size="small" round plain type="primary" @click="fetchList()">重试</van-button>
            </div>
            <van-empty v-else description="暂无消息" />
          </div>
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
import AppHero from '@/components/AppHero.vue'

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
.notify-page { display: flex; flex-direction: column; }
.pull-fill { min-height: 100%; }
.pull-inner { padding-bottom: 60px; }
.read-all { color: #fff; }
.hero-sub {
  font-size: 11.5px; margin-top: 6px; opacity: .95;
  text-shadow: 0 1px 3px rgba(8, 44, 96, .26);
}

/* 一条消息：图标 + 正文 + 右箭头，未读左侧有强调色边 */
.msg { display: flex; align-items: flex-start; gap: 11px; padding: 13px 14px; cursor: pointer; }
.msg.unread { box-shadow: 0 4px 14px rgba(17, 45, 90, .07), inset 3px 0 0 #1677e0; }
.msg-ic {
  width: 34px; height: 34px; border-radius: 12px; flex-shrink: 0;
  background: #eaf3ff; color: #1677e0;
  display: flex; align-items: center; justify-content: center;
}
.msg-bd { flex: 1; min-width: 0; }
.msg-title { display: flex; align-items: flex-start; gap: 6px; }
.msg-text {
  font-size: 13.5px; color: #23262b; line-height: 1.5; font-weight: 600;
  word-break: break-word;
}
.dot {
  width: 7px; height: 7px; border-radius: 50%; background: #ee0a24;
  flex-shrink: 0; margin-top: 6px;
}
.msg-time { font-size: 11px; color: #98a1ad; margin-top: 5px; }
.msg-go { color: #c6cfdd; margin-top: 8px; flex-shrink: 0; }

.more-hint { text-align: center; color: #98a1ad; font-size: 12px; padding: 4px 0 14px; }
.notify-failed {
  display: flex; flex-direction: column; align-items: center; gap: 8px;
  padding: 60px 0; color: #969799; font-size: 13px;
}
</style>
