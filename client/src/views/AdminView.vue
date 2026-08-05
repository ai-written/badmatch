<template>
  <div class="admin-page">
    <van-nav-bar title="用户管理" left-text="返回" left-arrow @click-left="goBack" />

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

    <van-dialog v-model:show="showReset" title="重置密码" show-cancel-button @confirm="doResetPwd">
      <van-field v-model="resetPwd" type="password" placeholder="新密码（至少6位）" style="margin:10px 0" />
    </van-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useAuthStore } from '@/stores/auth'
import { useGoBack } from '@/composables/useGoBack'
import api from '@/api/client'
import { showToast, showConfirmDialog } from 'vant'

const auth = useAuthStore()
const { goBack } = useGoBack()
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
onMounted(async () => {
  await auth.fetchMe()
  await fetchUsers()
})
</script>

<style scoped>
.admin-page { height: 100vh; display: flex; flex-direction: column; background: #f5f6f8; }
.admin-scroll { flex: 1; overflow-y: auto; padding-top: 12px; }
.pull-fill { min-height: 100%; }
.pull-inner { padding-bottom: 60px; }
.user-avatar { margin-right: 10px; flex-shrink: 0; }
</style>
