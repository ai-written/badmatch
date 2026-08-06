<template>
  <div class="admin-page">
    <van-nav-bar title="管理面板" left-text="返回" left-arrow @click-left="goBack" />

    <van-tabs v-model:active="adminTab" color="#1989fa" class="admin-tabs" :show-header="isSuper">
      <van-tab title="用户管理">
        <div class="admin-scroll">
          <van-pull-refresh v-model="refreshing" @refresh="onRefresh" class="pull-fill">
            <div class="pull-inner">
              <van-cell-group inset v-for="u in users" :key="u.id" style="margin-bottom:4px">
                <van-cell :title="u.username" :label="`ID:${u.id}  ${roleLabel(u.role)}  ${u.gender === 'M' ? '男' : u.gender === 'F' ? '女' : '-'}${u.invited_by_username ? ` 邀请人：${u.invited_by_username}` : ''}`">
                  <template #icon>
                    <van-image round width="36" height="36" :src="u.avatar || defaultAvatar" class="user-avatar" />
                  </template>
                  <template #value>
                    <van-button v-if="isSuper && u.role !== 'superadmin' && u.id !== auth.user?.id" size="small" type="warning" @click="toggleRole(u)">{{ u.role === 'admin' ? '取消管理员' : '设为管理员' }}</van-button>
                    <van-button v-if="u.id !== auth.user?.id && (isSuper || u.invited_by === auth.user?.id)" size="small" type="danger" style="margin-left:6px" @click="doDelete(u)">删除</van-button>
                  </template>
                </van-cell>
                <van-cell v-if="isSuper && u.id !== auth.user?.id" title="重置密码" is-link @click="openResetPwd(u)" />
              </van-cell-group>
            </div>
          </van-pull-refresh>
        </div>
      </van-tab>

      <van-tab v-if="isSuper" title="操作日志">
        <div class="admin-scroll audit-scroll">
          <div class="audit-filter">
            <van-field v-model="auditFilter.username" label="用户名" placeholder="模糊搜索" clearable @clear="resetAuditList">
              <template #button>
                <van-button size="small" type="primary" @click="resetAuditList">查询</van-button>
              </template>
            </van-field>
            <van-cell title="操作类型" is-link :value="auditFilter.action ? ACTION_LABELS[auditFilter.action] || auditFilter.action : '全部'" @click="showActionSheet = true" />
            <van-cell title="时间范围" is-link :value="auditFilter.dateRange || '全部'" @click="showCalendar = true" />
            <van-cell v-if="auditFilter.action || auditFilter.username || auditFilter.dateRange" title="重置筛选" is-link @click="resetAuditFilter" />
          </div>

          <div class="audit-count" v-if="auditTotal > 0">共 {{ auditTotal }} 条</div>

          <van-list v-model:loading="auditLoading" :finished="auditFinished" finished-text="没有更多了" @load="loadAuditLogs">
            <van-cell-group inset v-for="a in auditLogs" :key="a.id" style="margin-bottom:4px">
              <van-cell :title="`${a.username || '匿名'} · ${ACTION_LABELS[a.action] || a.action}`" :label="`${formatTime(a.created_at)}${a.ip ? '  IP:' + a.ip : ''}`" is-link @click="openAuditDetail(a)" />
            </van-cell-group>
          </van-list>
        </div>
      </van-tab>

      <van-tab v-if="isSuper" title="访问日志">
        <div class="admin-scroll audit-scroll">
          <div class="audit-filter">
            <van-field v-model="accessFilter.keyword" label="关键词" placeholder="IP/路径/用户名/方法/状态码" clearable @clear="resetAccessList">
              <template #button>
                <van-button size="small" type="primary" @click="resetAccessList">查询</van-button>
              </template>
            </van-field>
            <van-cell title="请求方法" is-link :value="accessFilter.method || '全部'" @click="showMethodSheet = true" />
            <van-cell v-if="accessFilter.keyword || accessFilter.method" title="重置筛选" is-link @click="resetAccessFilter" />
          </div>

          <div class="audit-count" v-if="accessTotal > 0">共 {{ accessTotal }} 条（实时浏览日志）</div>

          <van-list v-model:loading="accessLoading" :finished="accessFinished" finished-text="没有更多了" @load="loadAccessLogs">
            <van-cell-group inset v-for="(a, i) in accessLogs" :key="i" style="margin-bottom:4px">
              <van-cell :title="`${a.method} ${a.path}${a.query ? '?' + a.query : ''}`" :label="`${a.time}  ${a.ip || '无IP'}  ${a.status}  ${a.duration_ms}ms${a.username ? '  ' + a.username : ''}`" is-link @click="openAccessDetail(a)" />
            </van-cell-group>
          </van-list>
        </div>
      </van-tab>
    </van-tabs>

    <van-dialog v-model:show="showReset" title="重置密码" show-cancel-button @confirm="doResetPwd">
      <van-field v-model="resetPwd" type="password" placeholder="新密码（至少6位）" style="margin:10px 0" />
    </van-dialog>

    <van-action-sheet v-model:show="showActionSheet" :actions="actionOptions" cancel-text="取消" @select="onActionSelect" />
    <van-calendar v-model:show="showCalendar" type="range" color="#1989fa" :max-range="90" @confirm="onCalendarConfirm" />
    <van-dialog v-model:show="showAuditDetail" title="操作详情" :show-confirm-button="false">
      <div class="audit-detail">
        <p><b>时间：</b>{{ auditDetail && formatTime(auditDetail.created_at) }}</p>
        <p><b>用户：</b>{{ auditDetail && (auditDetail.username || '匿名') }}（ID:{{ auditDetail && auditDetail.user_id }}）</p>
        <p><b>操作：</b>{{ auditDetail && (ACTION_LABELS[auditDetail.action] || auditDetail.action) }}</p>
        <p v-if="auditDetail && auditDetail.target_type"><b>对象：</b>{{ auditDetail.target_type }} #{{ auditDetail.target_id }}</p>
        <p v-if="auditDetail && auditDetail.ip"><b>IP：</b>{{ auditDetail.ip }}</p>
        <div v-if="auditDetail && auditDetail.detail && Object.keys(auditDetail.detail).length">
          <b>详情：</b>
          <pre class="audit-json">{{ JSON.stringify(auditDetail.detail, null, 2) }}</pre>
        </div>
        <van-button size="small" type="primary" block style="margin-top:12px" @click="showAuditDetail = false">关闭</van-button>
      </div>
    </van-dialog>

    <van-action-sheet v-model:show="showMethodSheet" :actions="methodOptions" cancel-text="取消" @select="onMethodSelect" />
    <van-dialog v-model:show="showAccessDetail" title="访问详情" :show-confirm-button="false">
      <div class="audit-detail">
        <p v-for="(v, k) in accessDetail" :key="k"><b>{{ k }}：</b>{{ typeof v === 'object' ? JSON.stringify(v) : v }}</p>
        <van-button size="small" type="primary" block style="margin-top:12px" @click="showAccessDetail = false">关闭</van-button>
      </div>
    </van-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted } from 'vue'
import { useAuthStore } from '@/stores/auth'
import { useGoBack } from '@/composables/useGoBack'
import api from '@/api/client'
import { showToast, showConfirmDialog } from 'vant'

const auth = useAuthStore()
const { goBack } = useGoBack()
const adminTab = ref(0)
const refreshing = ref(false)
const users = ref<any[]>([])
const showReset = ref(false)
const resetPwd = ref('')
const resetUserId = ref(0)
const defaultAvatar = 'https://img.yzcdn.cn/vant/cat.jpeg'

const isSuper = computed(() => auth.user?.role === 'superadmin')

function roleLabel(role: string) {
  return role === 'superadmin' ? '超级管理员' : role === 'admin' ? '管理员' : '用户'
}

async function fetchUsers(skipLoading = false) {
  const url = isSuper.value ? '/auth/admin/users' : '/auth/admin/selectable-users'
  const res = await api.get(url, { skipLoading } as any)
  users.value = res.data
}

async function toggleRole(u: any) {
  const newRole = u.role === 'admin' ? 'user' : 'admin'
  await api.post('/auth/admin/set-role', { user_id: u.id, role: newRole })
  showToast(newRole === 'admin' ? '已设为管理员' : '已取消管理员')
  await fetchUsers()
}

async function doDelete(u: any) {
  try { await showConfirmDialog({ title: '确认删除', message: `确定删除用户 ${u.username}？` }) } catch { return }
  await api.delete(`/auth/admin/users/${u.id}`)
  showToast('已删除')
  await fetchUsers()
}

function openResetPwd(u: any) {
  resetUserId.value = u.id
  resetPwd.value = ''
  showReset.value = true
}

async function doResetPwd() {
  if (resetPwd.value.length < 6) { showToast('密码至少6位'); return }
  await api.post('/auth/admin/reset-password', { user_id: resetUserId.value, new_password: resetPwd.value })
  showToast('密码已重置')
}

async function onRefresh() {
  try { await fetchUsers(true) } finally { refreshing.value = false }
}

// ---- 操作日志 ----
const ACTION_LABELS: Record<string, string> = {
  register: '注册', login_success: '登录成功', login_failed: '登录失败', logout: '登出',
  change_password: '修改密码', admin_reset_password: '重置密码', admin_set_role: '设置角色',
  admin_delete_user: '删除用户', tournament_create: '创建赛事', tournament_create_batch: '批量创建赛事',
  tournament_delete: '删除赛事', tournament_start: '开始赛事', tournament_end: '结束赛事',
  tournament_withdraw: '退赛', registration: '报名', cancel_registration: '取消报名',
  round_start: '开始轮次', match_force_end: '结束比赛', match_score_update: '记分',
  support_vote: '投票', referee_claim: '认领裁判', referee_release: '释放裁判',
  update_profile: '修改资料', upload_avatar: '上传头像', generate_invite: '生成邀请码',
}

const actionOptions = computed(() => [
  { name: '全部', value: '' },
  ...Object.entries(ACTION_LABELS).map(([value, name]) => ({ name, value })),
])

const auditLogs = ref<any[]>([])
const auditPage = ref(1)
const auditLoading = ref(false)
const auditFinished = ref(false)
const auditTotal = ref(0)
// 防重入标志：van-list 触发 @load 时已把 auditLoading 置为 true，
// 不能再用它做防重入判断（否则接口永不调用）
let auditRequesting = false
const auditFilter = reactive({ username: '', action: '', dateRange: '' })
const showActionSheet = ref(false)
const showCalendar = ref(false)
const showAuditDetail = ref(false)
const auditDetail = ref<any>(null)

function pad(n: number) { return n < 10 ? `0${n}` : String(n) }

function formatTime(iso: string) {
  if (!iso) return ''
  const d = new Date(iso)
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

async function loadAuditLogs() {
  if (auditRequesting) return
  auditRequesting = true
  try {
    const params: any = { page: auditPage.value, page_size: 20 }
    if (auditFilter.action) params.action = auditFilter.action
    const uname = auditFilter.username.trim()
    if (uname) params.username = uname
    if (auditFilter.dateRange) {
      const parts = auditFilter.dateRange.split(' ~ ')
      if (parts[0]) params.created_from = `${parts[0]} 00:00:00`
      if (parts[1]) params.created_to = `${parts[1]} 23:59:59`
    }
    const res = await api.get('/auth/admin/audit-logs', { params, skipLoading: true } as any)
    const data = res.data
    auditLogs.value = auditPage.value === 1 ? data.items : [...auditLogs.value, ...data.items]
    auditTotal.value = data.total
    auditFinished.value = auditLogs.value.length >= data.total
    auditPage.value += 1
  } catch {
    auditFinished.value = true
  } finally {
    auditRequesting = false
    // 通知 van-list 本次加载完成（由它自行判断是否继续加载）
    auditLoading.value = false
  }
}

function resetAuditList() {
  auditLogs.value = []
  auditPage.value = 1
  auditTotal.value = 0
  auditFinished.value = false
  loadAuditLogs()
}

function resetAuditFilter() {
  auditFilter.username = ''
  auditFilter.action = ''
  auditFilter.dateRange = ''
  resetAuditList()
}

function onActionSelect(item: any) {
  showActionSheet.value = false
  auditFilter.action = item.value || ''
  resetAuditList()
}

function onCalendarConfirm(dates: Date[]) {
  showCalendar.value = false
  if (dates && dates.length === 2 && dates[0] && dates[1]) {
    const fmt = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
    auditFilter.dateRange = `${fmt(dates[0])} ~ ${fmt(dates[1])}`
  } else {
    auditFilter.dateRange = ''
  }
  resetAuditList()
}

function openAuditDetail(a: any) {
  auditDetail.value = a
  showAuditDetail.value = true
}

// ---- 访问日志 ----
const methodOptions = [
  { name: '全部', value: '' },
  { name: 'GET', value: 'GET' },
  { name: 'POST', value: 'POST' },
  { name: 'PUT', value: 'PUT' },
  { name: 'DELETE', value: 'DELETE' },
]

const accessLogs = ref<any[]>([])
const accessPage = ref(1)
const accessLoading = ref(false)
const accessFinished = ref(false)
const accessTotal = ref(0)
const accessFilter = reactive({ keyword: '', method: '' })
const showMethodSheet = ref(false)
const showAccessDetail = ref(false)
const accessDetail = ref<any>(null)
// 防重入标志（van-list 触发 @load 时已把 loading 置 true）
let accessRequesting = false

async function loadAccessLogs() {
  if (accessRequesting) return
  accessRequesting = true
  try {
    const params: any = { page: accessPage.value, page_size: 20 }
    if (accessFilter.method) params.method = accessFilter.method
    const kw = accessFilter.keyword.trim()
    if (kw) params.keyword = kw
    const res = await api.get('/auth/admin/access-logs', { params, skipLoading: true } as any)
    const data = res.data
    accessLogs.value = accessPage.value === 1 ? data.items : [...accessLogs.value, ...data.items]
    accessTotal.value = data.total
    accessFinished.value = accessLogs.value.length >= data.total
    accessPage.value += 1
  } catch {
    accessFinished.value = true
  } finally {
    accessRequesting = false
    accessLoading.value = false
  }
}

function resetAccessList() {
  accessLogs.value = []
  accessPage.value = 1
  accessTotal.value = 0
  accessFinished.value = false
  loadAccessLogs()
}

function resetAccessFilter() {
  accessFilter.keyword = ''
  accessFilter.method = ''
  resetAccessList()
}

function onMethodSelect(item: any) {
  showMethodSheet.value = false
  accessFilter.method = item.value || ''
  resetAccessList()
}

function openAccessDetail(a: any) {
  accessDetail.value = a
  showAccessDetail.value = true
}

onMounted(async () => {
  await auth.fetchMe()
  await fetchUsers()
})
</script>

<style scoped>
.admin-page { height: 100vh; display: flex; flex-direction: column; background: #f5f6f8; }
.admin-tabs { flex: 1; display: flex; flex-direction: column; overflow: hidden; }
.admin-tabs :deep(.van-tabs__content) { flex: 1; overflow: hidden; }
.admin-tabs :deep(.van-tab__panel) { height: 100%; }
.admin-scroll { height: 100%; overflow-y: auto; padding-top: 12px; }
.pull-fill { min-height: 100%; }
.pull-inner { padding-bottom: 60px; }
.user-avatar { margin-right: 10px; flex-shrink: 0; }
.audit-filter { margin-bottom: 8px; }
.audit-count { padding: 4px 16px 8px; color: #969799; font-size: 12px; }
.audit-scroll { padding-top: 0; }
.audit-detail { padding: 4px 16px 16px; font-size: 13px; color: #323233; }
.audit-detail p { margin: 6px 0; word-break: break-all; }
.audit-json { background: #f7f8fa; border-radius: 6px; padding: 8px; margin: 6px 0 0; font-size: 12px; overflow-x: auto; white-space: pre-wrap; word-break: break-all; }
</style>
