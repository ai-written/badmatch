<template>
  <div class="admin-page">
    <van-nav-bar title="用户管理" left-text="返回" left-arrow @click-left="$router.back()" />

    <van-cell-group inset v-for="u in users" :key="u.id" style="margin-bottom:4px">
      <van-cell :title="u.username" :label="`ID:${u.id}  ${u.role === 'admin' ? '管理员' : '用户'}  ${u.gender === 'M' ? '男' : u.gender === 'F' ? '女' : '-'}`">
        <template #value>
          <van-button v-if="u.id !== auth.user?.id" size="small" type="danger" @click="doDelete(u)">删除</van-button>
        </template>
      </van-cell>
      <van-cell v-if="u.id !== auth.user?.id" title="重置密码" is-link @click="openResetPwd(u)" />
    </van-cell-group>

    <van-dialog v-model:show="showReset" title="重置密码" show-cancel-button @confirm="doResetPwd">
      <van-field v-model="resetPwd" type="password" placeholder="新密码（至少6位）" style="margin:10px 0" />
    </van-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useAuthStore } from '@/stores/auth'
import api from '@/api/client'
import { showToast, showConfirmDialog } from 'vant'

const auth = useAuthStore()
const users = ref<any[]>([])
const showReset = ref(false)
const resetPwd = ref('')
const resetUserId = ref(0)

async function fetchUsers() {
  const res = await api.get('/auth/admin/users')
  users.value = res.data
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

onMounted(() => fetchUsers())
</script>

<style scoped>
.admin-page { height: 100vh; overflow-y: auto; background: #f5f6f8; padding-bottom: 60px; }
</style>
