<template>
  <div class="profile-page">
    <template v-if="!auth.user">
      <div class="form-scroll">
      <van-tabs v-model:active="loginTab" class="auth-tabs" color="#1989fa">
        <van-tab title="登录" />
        <van-tab title="注册" />
      </van-tabs>

      <van-form v-if="loginTab === 0" @submit="onLogin" class="auth-form">
        <van-cell-group inset>
          <van-field v-model="loginForm.username" label="用户名" placeholder="请输入用户名" required :rules="[{ required: true, message: '请输入用户名' }]" />
          <van-field v-model="loginForm.password" label="密码" placeholder="请输入密码" type="password" required :rules="[{ required: true, message: '请输入密码' }]" />
        </van-cell-group>
        <div class="form-submit"><van-button round block type="primary" native-type="submit" :loading="submitting">登录</van-button></div>
      </van-form>

      <van-form v-else @submit="onRegister" class="auth-form">
        <van-cell-group inset>
          <van-field v-model="registerForm.username" label="用户名" placeholder="请输入用户名" required :rules="[{ required: true, message: '请输入用户名' }]" />
          <van-field name="gender" label="性别">
            <template #input>
              <van-radio-group v-model="registerForm.gender" direction="horizontal">
                <van-radio name="M">男</van-radio>
                <van-radio name="F">女</van-radio>
              </van-radio-group>
            </template>
          </van-field>
          <van-field v-model="registerForm.invite_code" label="邀请码" :placeholder="hasUsers ? '注册邀请码（必填）' : '注册邀请码（首个用户可跳过）'" :required="hasUsers" />
          <van-field v-model="registerForm.password" label="密码" placeholder="至少6位" type="password" required :rules="[{ required: true, message: '请输入密码' }, { pattern: /^.{6,}$/, message: '密码至少6位' }]" />
        </van-cell-group>
        <div class="form-submit"><van-button round block type="primary" native-type="submit" :loading="submitting">注册</van-button></div>
      </van-form>
      </div>
    </template>

    <template v-else>
      <div class="profile-scroll">
      <van-pull-refresh v-model="refreshing" @refresh="onRefresh" class="pull-fill">
      <div class="pull-inner">
      <div class="profile-header">
        <div class="avatar-wrapper" @click="triggerUpload">
          <van-image round width="72" height="72" :src="auth.user.avatar || defaultAvatar" />
          <div class="avatar-overlay"><van-icon name="photograph" size="20" color="#fff" /></div>
        </div>
        <h3>{{ auth.user.username }}</h3>
        <input ref="fileInput" type="file" accept="image/*" style="display:none" @change="onFileChange" />
      </div>

      <van-cell-group inset>
        <van-cell title="用户名" is-link :value="auth.user.username" @click="newUsername = auth.user?.username || ''; showEditName = true" />
        <van-cell title="性别" is-link :value="auth.user.gender === 'M' ? '男' : auth.user.gender === 'F' ? '女' : '未设置'" @click="editGender = auth.user?.gender || ''; showEditGender = true" />
      </van-cell-group>

      <van-cell-group inset style="margin-top:12px">
        <van-cell title="修改密码" is-link @click="showEditPwd = true" />
        <van-cell title="总场次" :value="String(stats.total_matches)" />
        <van-cell title="胜场" :value="String(stats.total_wins)" />
        <van-cell title="胜率" :value="`${stats.win_rate}%`" label="双打胜率" />
        <van-cell title="参赛次数" :value="String(stats.tournaments_played)" />
      </van-cell-group>

      <van-cell-group inset style="margin-top:12px" v-if="auth.user?.role === 'admin'">
        <van-cell title="用户管理" is-link to="/admin" />
      </van-cell-group>

      <van-cell-group inset style="margin-top:12px">
        <van-cell title="邀请码" :value="inviteCode || '点击生成'" @click="generateInvite" clickable />
        <van-cell v-if="inviteCode" title="邀请链接" label="点击复制" :value="inviteLink" @click="copyInviteLink" clickable />
      </van-cell-group>

      <div class="logout-btn">
        <van-button plain type="danger" block @click="auth.logout()">退出登录</van-button>
      </div>
      </div>
      </van-pull-refresh>
      </div>
    </template>

    <van-dialog v-model:show="showEditGender" title="修改性别" show-cancel-button @confirm="saveGender">
      <van-radio-group v-model="editGender" style="margin:10px;display:flex;justify-content:center;gap:20px">
        <van-radio name="M">男</van-radio>
        <van-radio name="F">女</van-radio>
      </van-radio-group>
    </van-dialog>

    <van-dialog v-model:show="showEditPwd" title="修改密码" show-cancel-button @confirm="savePassword">
      <van-field v-model="oldPwd" type="password" placeholder="原密码" style="margin:8px 0" />
      <van-field v-model="newPwd" type="password" placeholder="新密码（至少6位）" />
    </van-dialog>

    <van-dialog v-model:show="showEditName" title="修改用户名" show-cancel-button @confirm="saveUsername">
      <van-field v-model="newUsername" placeholder="新用户名" style="margin:10px 0" />
    </van-dialog>
  </div>
</template>

<script setup lang="ts">
import { reactive, ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import api from '@/api/client'
import { showToast } from 'vant'

const router = useRouter()
const auth = useAuthStore()
const hasUsers = ref(false)
const loginTab = ref(0)
const refreshing = ref(false)
const submitting = ref(false)
const fileInput = ref<HTMLInputElement | null>(null)
const defaultAvatar = 'https://img.yzcdn.cn/vant/cat.jpeg'

const loginForm = reactive({ username: '', password: '' })
const registerForm = reactive({ username: '', password: '', gender: '', invite_code: '' })
const stats = reactive({ total_matches: 0, total_wins: 0, win_rate: 0, tournaments_played: 0 })

const inviteCode = ref('')
const inviteLink = computed(() => inviteCode.value ? `${window.location.origin}${window.location.pathname}?invite=${inviteCode.value}` : '')

const showEditGender = ref(false); const editGender = ref('')
const showEditPwd = ref(false); const oldPwd = ref(''); const newPwd = ref('')
const showEditName = ref(false); const newUsername = ref('')

async function fetchStats() {
  try { const res = await api.get('/auth/stats'); Object.assign(stats, res.data) } catch {}
}

function doRedirect() {
  const r = sessionStorage.getItem('loginRedirect'); if (r) { sessionStorage.removeItem('loginRedirect'); router.replace(r) }
}

function triggerUpload() { fileInput.value?.click() }

async function onFileChange(e: Event) {
  const f = (e.target as HTMLInputElement).files?.[0]; if (!f) return
  const fd = new FormData(); fd.append('file', f)
  try { const res = await api.post('/auth/upload-avatar', fd, { headers: { 'Content-Type': 'multipart/form-data' } }); auth.user!.avatar = res.data.avatar; showToast('头像更新成功') } catch {}
}

async function onLogin() {
  submitting.value = true
  try { await auth.login(loginForm.username, loginForm.password); showToast('登录成功'); await fetchStats(); doRedirect() } catch {} finally { submitting.value = false }
}

async function onRegister() {
  submitting.value = true
  try { await auth.register(registerForm.username, registerForm.password, registerForm.gender, registerForm.invite_code); showToast('注册成功'); await fetchStats(); doRedirect() } catch {} finally { submitting.value = false }
}

async function generateInvite() {
  try { const res = await api.post('/auth/generate-invite'); inviteCode.value = res.data.invite_code; showToast('已生成') } catch {}
}

async function copyInviteLink() {
  if (!inviteLink.value) return; await navigator.clipboard.writeText(inviteLink.value); showToast('已复制')
}

function saveGender() {
  if (!editGender.value) return
  api.put('/auth/me/profile', { gender: editGender.value }).then(() => { auth.user!.gender = editGender.value; showToast('已修改') }).catch(() => {})
}

async function savePassword() {
  if (!oldPwd.value || !newPwd.value) return
  try { await api.put('/auth/me/password', { old_password: oldPwd.value, new_password: newPwd.value }); showToast('密码已修改'); oldPwd.value = ''; newPwd.value = '' } catch {}
}

async function saveUsername() {
  if (!newUsername.value) return
  try {
    await api.put('/auth/me/profile', { username: newUsername.value })
    auth.user!.username = newUsername.value
    showToast('用户名已修改')
    newUsername.value = ''
  } catch {}
}

async function onRefresh() {
  try {
    await fetchStats()
    await auth.fetchMe()
    if (auth.user?.invite_code) inviteCode.value = auth.user.invite_code
  } finally {
    refreshing.value = false
  }
}

onMounted(async () => {
  try { const r = await api.get('/auth/has-users'); hasUsers.value = r.data.exists } catch {}
  const urlParams = new URLSearchParams(window.location.search)
  const ip = urlParams.get('invite')
  if (ip) { loginTab.value = 1; registerForm.invite_code = ip }
  await auth.fetchMe()
  if (auth.user) { await fetchStats(); if (auth.user.invite_code) inviteCode.value = auth.user.invite_code }
})
</script>

<style scoped>
.profile-page { height: 100vh; display: flex; flex-direction: column; background: #f5f6f8; }
.profile-scroll { flex: 1; overflow-y: auto; }
.pull-fill { min-height: 100%; }
.pull-inner { padding-bottom: 80px; }
.form-scroll { flex: 1; overflow-y: auto; padding-bottom: 80px; }
.auth-tabs { margin-top: 0; }
.auth-form { margin-top: 16px; }
.form-submit { margin: 16px; }
.profile-header { display: flex; flex-direction: column; align-items: center; padding: 30px 0 20px; }
.avatar-wrapper { position: relative; cursor: pointer; }
.avatar-overlay { position: absolute; inset: 0; border-radius: 50%; background: rgba(0,0,0,.35); display: flex; align-items: center; justify-content: center; opacity: 0; transition: opacity .2s; }
.avatar-wrapper:hover .avatar-overlay { opacity: 1; }
.profile-header h3 { margin-top: 10px; font-size: 18px; }
.logout-btn { padding: 20px 16px; }
</style>
