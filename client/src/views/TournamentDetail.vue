<template>
  <div class="detail-page">
    <van-nav-bar title="赛事详情" left-text="返回" left-arrow @click-left="goBack">
      <template #right>
        <van-icon v-if="canDelete" name="delete-o" size="20" @click="doDelete" />
      </template>
    </van-nav-bar>

    <div class="detail-scroll">
    <van-pull-refresh v-model="refreshing" @refresh="onRefresh" class="pull-fill">
      <div class="pull-inner">
    <van-loading v-if="!tournament" class="loading" />
    <template v-else>
      <div class="info-card">
        <div class="info-head">
          <h2>{{ tournament.title }}</h2>
          <van-tag :type="statusType" size="medium" round>{{ tournament.status === 'open' ? '报名中' : tournament.status === 'ongoing' ? '进行中' : '已结束' }}</van-tag>
        </div>
        <p class="info-desc" v-if="tournament.description">{{ tournament.description }}</p>
        <div class="info-grid">
          <div class="ig-item"><van-icon name="location-o" /><span>{{ tournament.location || '待定' }}</span></div>
          <div class="ig-item" v-if="tournament.courts && tournament.courts.length > 0"><van-icon name="guide-o" /><span>场地号{{ tournament.courts[0].name }}</span></div>
          <div class="ig-item"><van-icon name="clock-o" /><span>{{ fmtDateTime(tournament.start_date, tournament.end_date) }}</span></div>
          <div class="ig-item"><van-icon name="friends-o" /><span>{{ tournament.registered_count }}/{{ tournament.max_participants }} 人</span></div>
          <div class="ig-item"><van-icon name="medal-o" /><span>{{ tournament.points_to_win || 11 }} 分制</span></div>
        </div>
      </div>

      <div class="action-block" v-if="tournament.status === 'open'">
        <van-button type="primary" block round :disabled="tournament.is_registered" @click="doRegister">
          {{ tournament.is_registered ? '已报名' : '立即报名' }}
        </van-button>
        <van-button v-if="tournament.is_registered && tournament.status === 'open'" plain block round style="margin-top:8px" @click="doCancelRegister">
          取消报名
        </van-button>
      </div>

      <div class="action-block" v-if="tournament.is_registered && tournament.status === 'ongoing'">
        <van-button type="warning" plain block round @click="doWithdraw">
          退出比赛
        </van-button>
      </div>

      <div class="player-section">
        <div class="player-head">
          <span class="ph-title">已报名 ({{ tournament.registered_count }})</span>
        </div>
        <div class="player-grid" v-if="registrations.length > 0">
          <div v-for="r in registrations" :key="r.id" class="player-chip" @click.stop="viewPlayer(r)">
            <div class="avatar-badge-sm">
              <van-image round width="40" height="40" :src="r.avatar || defaultAvatar" />
              <span v-if="r.user_id === tournament.creator_id" class="host-badge">房主</span>
            </div>
            <span class="player-name">{{ r.username }}</span>
            <span class="player-time">{{ formatTime(r.created_at) }}</span>
          </div>
        </div>
        <p v-else class="empty-hint">暂无报名</p>
      </div>

      <div class="creator-block" v-if="isCreator && tournament.status === 'ongoing'">
        <van-button type="danger" block round @click="doEndTournament">提前结束赛事</van-button>
      </div>

      <div class="nav-block" v-if="tournament.status !== 'open'">
        <van-grid :column-num="2" clickable :border="false">
          <van-grid-item icon="clock-o" text="对阵表" @click="$router.push(`/tournament/${tournament.id}/schedule`)" />
          <van-grid-item icon="chart-trending-o" text="积分榜" @click="$router.push(`/tournament/${tournament.id}/rankings`)" />
        </van-grid>
      </div>

      <div class="creator-block" v-if="isCreator && tournament.status === 'open'">
        <van-button type="danger" block round @click="doStart" :disabled="tournament.registered_count < 4">
          开始比赛（需满 {{ Math.max(4 - tournament.registered_count, 0) }} 人）
        </van-button>
      </div>

    <van-popup v-model:show="showPlayerStats" round position="bottom" :style="{ height: '65%' }" class="stats-popup" lock-scroll>
       <div class="popup-content" @touchmove.stop>
          <div class="popup-player-head">
            <van-image round width="56" height="56" :src="playerDetail.avatar || defaultAvatar" />
            <h3>{{ playerDetail.username }}</h3>
          </div>
          <van-cell-group inset v-if="playerStats.total_matches > 0">
            <van-cell title="总场次" :value="String(playerStats.total_matches)" />
            <van-cell title="胜场" :value="String(playerStats.total_wins)" />
            <van-cell title="负场" :value="String(playerStats.total_matches - playerStats.total_wins)" />
            <van-cell title="胜率" :value="`${playerStats.win_rate}%`">
              <template #label>双打胜率，按参与场次统计</template>
            </van-cell>
            <van-cell title="参赛次数" :value="String(playerStats.tournaments_played)" />
          </van-cell-group>
          <van-empty v-else description="暂无比赛记录" />
        </div>
      </van-popup>

    </template>
      </div>
    </van-pull-refresh>
    </div>
  </div>
      <!-- 场次重选弹窗 -->
      <van-popup v-model:show="showMatchPicker" position="bottom" round :style="{ height: '45%' }">
        <div class="picker-toolbar">
          <span @click="showMatchPicker = false">取消</span>
          <span class="picker-title">当前报名 {{ tournament.registered_count }} 人，请选择总场次</span>
        </div>
        <van-cell-group inset style="margin-top:10px">
          <van-cell
            v-for="opt in matchOptions" :key="opt.total"
            :title="`${opt.total} 场`" :label="`每人 ${opt.per_person} 场`"
            @click="selectMatchStart(opt.total)" :class="{ active: matchTotal === opt.total }"
          />
        </van-cell-group>
      </van-popup>

    <van-popup v-model:show="showTransferPicker" round position="bottom" :style="{ height: '50%' }" lock-scroll>
      <div class="popup-content" @touchmove.stop>
        <h3>选择新房主</h3>
        <div class="popup-grid">
          <div
            v-for="r in registrations.filter(p => p.user_id !== auth.user?.id)"
            :key="r.id"
            class="transfer-player"
            :class="{ selected: selectedNewCreator === r.user_id }"
            @click="selectedNewCreator = r.user_id"
          >
            <van-image round width="40" height="40" :src="r.avatar || defaultAvatar" />
            <span>{{ r.username }}</span>
          </div>
        </div>
        <div style="padding: 12px 16px;">
          <van-button type="primary" block round :disabled="!selectedNewCreator" @click="doTransferAndWithdraw">确认转让并退出</van-button>
        </div>
      </div>
    </van-popup>

</template>

<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import api from '@/api/client'
import { useWebSocket } from '@/composables/useWebSocket'
import { useGoBack } from '@/composables/useGoBack'
import { showToast, showConfirmDialog } from 'vant'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const { goBack } = useGoBack()
const tid = Number(route.params.id)
const { lastMessage } = useWebSocket(tid)

const refreshing = ref(false)
const tournament = ref<any>(null)
const registrations = ref<any[]>([])
const showPlayerStats = ref(false)
const showMatchPicker = ref(false)
const showTransferPicker = ref(false)
const selectedNewCreator = ref(0)
const matchOptions = ref<{ total: number; per_person: number }[]>([])
const matchTotal = ref(0)
const playerDetail = ref<any>({})
const playerStats = ref<any>({})
const defaultAvatar = 'https://img.yzcdn.cn/vant/cat.jpeg'

const isCreator = computed(() => auth.user?.id === tournament.value?.creator_id)
const canDelete = computed(() => {
  if (!auth.user || !tournament.value) return false
  if (auth.user.role === 'admin' || auth.user.role === 'superadmin') return true
  return auth.user.id === tournament.value.creator_id && tournament.value.status === 'open'
})
const statusType = computed(() => tournament.value?.status === 'open' ? 'primary' : tournament.value?.status === 'ongoing' ? 'success' : 'default')

const WEEKDAYS2 = ['周日', '周一', '周二', '周三', '周四', '周五', '周六']
function fmtDateTime(start: string, end: string) {
  if (!start || !end) return ''
  const d = new Date(start)
  const y = d.getFullYear()
  const m = d.getMonth() + 1
  const day = d.getDate()
  const w = WEEKDAYS2[d.getDay()]
  return `${y}年${m}月${day}日(${w}) ${start.slice(11, 16)}~${end.slice(11, 16)}`
}
function formatTime(d: string) { if (!d) return ''; return d.replace('T', ' ').slice(5, 16) }

async function viewPlayer(r: any) {
  playerDetail.value = r
  showPlayerStats.value = true
  try {
    const res = await api.get(`/auth/stats/${r.user_id}`)
    playerStats.value = res.data
  } catch {
    playerStats.value = {}
  }
}

async function fetchDetail() {
  const res = await api.get(`/tournaments/${route.params.id}`)
  tournament.value = res.data
}
async function fetchRegistrations() {
  const res = await api.get(`/tournaments/${route.params.id}/registrations`)
  registrations.value = res.data
}
async function doRegister() {
  await api.post(`/tournaments/${route.params.id}/register`)
  showToast('报名成功')
  await Promise.all([fetchDetail(), fetchRegistrations()])
}
async function doCancelRegister() {
  await api.post(`/tournaments/${route.params.id}/cancel-register`)
  showToast('已取消报名')
  await Promise.all([fetchDetail(), fetchRegistrations()])
}

async function doWithdraw() {
  const iAmCreator = auth.user?.id === tournament.value?.creator_id
  if (iAmCreator) {
    showTransferPicker.value = true
    return
  } else {
    try { await showConfirmDialog({ title: '确认退出', message: '退出比赛后赛程将重新排列，确定退出？' }) } catch { return }
    await api.post(`/tournaments/${route.params.id}/withdraw/${auth.user!.id}`)
    showToast('已退出比赛')
    await Promise.all([fetchDetail(), fetchRegistrations()])
  }
}

async function doTransferAndWithdraw() {
  if (!selectedNewCreator.value) return
  try {
    const target = registrations.value.find(r => r.user_id === selectedNewCreator.value)
    await showConfirmDialog({
      title: '转让房主并退出',
      message: `退出后房主将转让给 ${target?.username || '所选用户'}，确定退出？`,
    })
  } catch { return }
  await api.post(`/tournaments/${route.params.id}/withdraw/${auth.user!.id}`, { new_creator_id: selectedNewCreator.value })
  showToast('已退出比赛')
  showTransferPicker.value = false
  await Promise.all([fetchDetail(), fetchRegistrations()])
}
async function fetchMatchOptionsForStart() {
  const n = tournament.value?.registered_count || 0
  if (n < 4) return
  try {
    const res = await api.get(`/tournaments/match-options/${n}`)
    matchOptions.value = res.data.options || []
  } catch {}
}

function selectMatchStart(total: number) {
  matchTotal.value = total
  showMatchPicker.value = false
  doStartWithTotal()
}

async function doStartWithTotal() {
  await api.post(`/tournaments/${route.params.id}/start`, { total_matches: Number(matchTotal.value) })
  showToast('比赛已开始')
  await fetchDetail()
}

async function doStart() {
  const count = tournament.value?.registered_count || 0
  const max = tournament.value?.max_participants || 0
  if (count < max) {
    await fetchMatchOptionsForStart()
    showMatchPicker.value = true
  } else {
    await api.post(`/tournaments/${route.params.id}/start`)
    showToast('比赛已开始')
    await fetchDetail()
  }
}
async function doDelete() {
  try { await showConfirmDialog({ title: '确认删除', message: '删除后不可恢复，确定要删除？' }) } catch { return }
  await api.delete(`/tournaments/${route.params.id}`)
  showToast('已删除')
  router.replace('/')
}

async function doEndTournament() {
  try { await showConfirmDialog({ title: '确认结束', message: '提前结束赛事？结束后无法恢复。' }) } catch { return }
  await api.post(`/tournaments/${route.params.id}/end-tournament`)
  showToast('赛事已结束')
  await fetchDetail()
}

async function onRefresh() {
  try {
    await Promise.all([fetchDetail(), fetchRegistrations()])
  } finally {
    refreshing.value = false
  }
}

watch(lastMessage, () => {
  fetchDetail()
  fetchRegistrations()
})

onMounted(async () => {
  await auth.fetchMe()
  await fetchDetail()
  await fetchRegistrations()
})
</script>

<style scoped>
.detail-page { height: 100vh; display: flex; flex-direction: column; background: #f5f6f8; }
.detail-scroll { flex: 1; overflow-y: auto; }
.pull-fill { min-height: 100%; }
.pull-inner { padding-bottom: 60px; }
.stats-popup { overflow: hidden !important; }
.loading { display: flex; justify-content: center; margin-top: 100px; }
.info-card { margin: 10px 12px; padding: 16px; background: #fff; border-radius: 10px; }
.info-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; }
.info-head h2 { font-size: 18px; font-weight: 700; }
.info-desc { color: #666; font-size: 13px; margin-bottom: 12px; }
.info-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px 12px; }
.ig-item { display: flex; align-items: center; gap: 4px; font-size: 12px; color: #888; }
.ig-item .van-icon { font-size: 13px; flex-shrink: 0; }
.action-block { padding: 10px 12px; }
.player-section { margin: 8px 12px; padding: 14px; background: #fff; border-radius: 10px; }
.player-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 10px; }
.ph-title { font-size: 15px; font-weight: 600; }
.player-grid { display: flex; flex-wrap: wrap; gap: 8px; }
.player-chip { display: flex; flex-direction: column; align-items: center; gap: 3px; width: 70px; cursor: pointer; }
.player-name { font-size: 12px; color: #666; text-align: center; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 64px; }
.avatar-badge-sm { position: relative; display: inline-block; }
.host-badge {
  position: absolute; top: -2px; left: -2px;
  font-size: 8px; color: #fff; background: #e74c3c;
  padding: 0 3px; border-radius: 6px; line-height: 14px; white-space: nowrap;
}
.player-time { font-size: 10px; color: #bbb; }
.player-chip.more { justify-content: center; font-size: 13px; color: #999; cursor: default; }
.empty-hint { font-size: 13px; color: #ccc; text-align: center; padding: 10px 0; }
.nav-block { margin: 8px 12px; }
.creator-block { padding: 10px 12px; }
.popup-content { padding: 20px; overflow-y: auto; max-height: 100%; }
.popup-content h3 { margin-bottom: 14px; font-size: 16px; }
.popup-grid { display: flex; flex-wrap: wrap; gap: 10px; }
.popup-player { display: flex; flex-direction: column; align-items: center; gap: 4px; width: 68px; cursor: pointer; }
.popup-player span { font-size: 12px; color: #666; }
.popup-time { font-size: 10px !important; color: #bbb !important; }
.popup-player-head { display: flex; flex-direction: column; align-items: center; margin-bottom: 16px; }
.transfer-player {
  display: flex; flex-direction: column; align-items: center; gap: 4px;
  width: 68px; cursor: pointer; padding: 6px; border-radius: 8px;
}
.transfer-player.selected { background: #e8f4ff; }
.transfer-player span { font-size: 12px; color: #666; }
.popup-player-head h3 { margin-top: 8px; }

.picker-toolbar { display: flex; align-items: center; justify-content: space-between; padding: 12px 16px; font-size: 15px; }
.picker-title { font-weight: 600; }
.van-cell.active { background: #e8f4ff; }
</style>
