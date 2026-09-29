<template>
  <div class="profile-page vh-page">
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
        <div class="forgot-link" @click="$router.push('/forgot-password')">忘记密码？</div>
      </van-form>

      <van-form v-else @submit="onRegister" class="auth-form">
        <van-cell-group inset>
          <van-field v-model="registerForm.username" label="用户名" placeholder="请输入用户名" required :rules="[{ required: true, message: '请输入用户名' }]" />
          <van-field v-model="registerForm.email" label="邮箱" type="email" placeholder="用于接收赛事通知" required :rules="[{ required: true, message: '请输入邮箱' }, { pattern: /^[^@\s]+@[^@\s]+\.[^@\s]+$/, message: '邮箱格式不正确' }]" />
          <van-field name="gender" label="性别">
            <template #input>
              <van-radio-group v-model="registerForm.gender" direction="horizontal">
                <van-radio name="M">男</van-radio>
                <van-radio name="F">女</van-radio>
              </van-radio-group>
            </template>
          </van-field>
          <van-field v-model="registerForm.invite_code" label="邀请码" :placeholder="hasUsers ? '注册邀请码（必填）' : '注册邀请码（首个用户可跳过）'" :required="hasUsers" />
          <van-field v-if="!hasUsers" v-model="registerForm.init_code" label="初始注册码" placeholder="部署时生成的超级管理员注册码" required :rules="[{ required: true, message: '请输入初始注册码' }]" />
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
          <van-image lazy-load round width="72" height="72" :src="auth.user.avatar || defaultAvatar" />
          <div class="avatar-overlay"><van-icon name="photograph" size="20" color="#fff" /></div>
        </div>
        <h3>{{ auth.user.username }}</h3>
        <input ref="fileInput" type="file" accept="image/*" style="display:none" @change="onFileChange" />
      </div>

      <van-cell-group inset>
        <van-cell title="用户名" is-link :value="auth.user.username" @click="newUsername = auth.user?.username || ''; showEditName = true" />
        <van-cell title="邮箱" is-link :value="auth.user?.email || '未设置'" @click="editEmail = auth.user?.email || ''; showEditEmail = true" />
        <van-cell title="性别" is-link :value="auth.user.gender === 'M' ? '男' : auth.user.gender === 'F' ? '女' : '未设置'" @click="editGender = auth.user?.gender || ''; showEditGender = true" />
      </van-cell-group>

      <van-cell-group inset style="margin-top:12px">
        <van-cell title="修改密码" is-link @click="showEditPwd = true" />
        <van-cell title="站内消息" is-link :value="unreadCount > 0 ? `${unreadCount} 条未读` : ''" to="/notifications" />
        <van-cell title="总场次" :value="String(stats.total_matches)" />
        <van-cell title="胜场" :value="String(stats.total_wins)" />
        <van-cell title="胜率" :value="`${stats.win_rate}%`" label="双打胜率" />
        <van-cell title="参赛次数" :value="String(stats.tournaments_played)" />
      </van-cell-group>

      <van-cell-group inset style="margin-top:12px" v-if="auth.user && auth.user.role !== 'user'">
        <van-cell title="用户管理" is-link to="/admin" />
      </van-cell-group>

      <van-cell-group inset style="margin-top:12px" v-if="canInvite">
        <!-- label 会被右侧的邀请码挤窄，所以文案要短（具体后果由确认弹窗说明）；
             另加 nowrap + 省略号兜底：窄屏上宁可截断也不折行 -->
        <van-cell class="invite-code-cell" title="邀请码" :label="inviteCode ? '点击重新生成' : ''" :value="inviteCode || '点击生成'" @click="generateInvite" clickable />
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
      <van-field v-model="newUsername" placeholder="新用户名" maxlength="64" style="margin:10px 0" />
    </van-dialog>

    <van-dialog v-model:show="showEditEmail" title="修改邮箱" show-cancel-button @confirm="saveEmail">
      <van-field v-model="editEmail" type="email" placeholder="邮箱（选填，留空可清除）" maxlength="255" style="margin:10px 0" />
    </van-dialog>
  </div>
</template>

<script setup lang="ts">
import { reactive, ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import api from '@/api/client'
import { showToast, showFailToast, showConfirmDialog } from 'vant'
import { compressAvatar } from '@/utils/avatar'
import { copyText } from '@/utils/clipboard'

const router = useRouter()
const auth = useAuthStore()
const hasUsers = ref(false)
const loginTab = ref(0)
const refreshing = ref(false)
const submitting = ref(false)
const fileInput = ref<HTMLInputElement | null>(null)
const defaultAvatar = 'https://img.yzcdn.cn/vant/cat.jpeg'

const loginForm = reactive({ username: '', password: '' })
const registerForm = reactive({ username: '', password: '', gender: '', invite_code: '', init_code: '', email: '' })
const stats = reactive({ total_matches: 0, total_wins: 0, win_rate: 0, tournaments_played: 0 })

const inviteCode = ref('')
const canInvite = computed(() => !!auth.user && (auth.user.role === 'admin' || auth.user.role === 'superadmin'))
const inviteLink = computed(() => inviteCode.value ? `${window.location.origin}${window.location.pathname}?invite=${inviteCode.value}` : '')
const unreadCount = ref(0)

const showEditGender = ref(false); const editGender = ref('')
const showEditPwd = ref(false); const oldPwd = ref(''); const newPwd = ref('')
const showEditName = ref(false); const newUsername = ref('')
const showEditEmail = ref(false); const editEmail = ref('')

async function fetchStats(skipLoading = false) {
  try { const res = await api.get('/auth/stats', { skipLoading } as any); Object.assign(stats, res.data) } catch {}
}

async function fetchUnread(skipLoading = false) {
  try {
    const res = await api.get('/notifications/unread-count', { skipLoading } as any)
    unreadCount.value = res.data.count || 0
  } catch {}
}

function doRedirect() {
  const r = sessionStorage.getItem('loginRedirect'); if (r) { sessionStorage.removeItem('loginRedirect'); router.replace(r) }
}

function triggerUpload() { fileInput.value?.click() }

const avatarUploading = ref(false)

async function onFileChange(e: Event) {
  const input = e.target as HTMLInputElement
  const f = input.files?.[0]
  // 先清空 value，否则连续选同一张图不会再触发 change
  input.value = ''
  if (!f || avatarUploading.value) return

  avatarUploading.value = true
  try {
    // 先在浏览器端压缩（2MB 原图 -> 约 20KB），避免低带宽下上传超时。
    // 压缩/校验阶段自己弹提示（这些错误没有 HTTP 响应，拦截器不会处理）
    let blob: Blob
    try {
      blob = await compressAvatar(f)
    } catch (err: any) {
      showFailToast(err?.message || '图片处理失败')
      return
    }

    // 请求阶段的错误交给全局 axios 拦截器提示（它会显示后端返回的中文 detail）。
    // 这里若再弹一次 err.message，会出现两条 toast，其中一条是英文的
    // 「Request failed with status code 429」。
    const fd = new FormData()
    fd.append('file', blob, 'avatar.jpg')
    const res = await api.post('/auth/upload-avatar', fd, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
    if (auth.user) auth.user.avatar = res.data.avatar
    showToast('头像更新成功')
  } catch {
    // 已由拦截器提示，这里静默
  } finally {
    avatarUploading.value = false
  }
}

async function onLogin() {
  submitting.value = true
  try { await auth.login(loginForm.username, loginForm.password); showToast('登录成功'); await fetchStats(); doRedirect() } catch {} finally { submitting.value = false }
}

async function onRegister() {
  submitting.value = true
  try { await auth.register(registerForm.username, registerForm.password, registerForm.gender, registerForm.email, registerForm.invite_code, registerForm.init_code); showToast('注册成功'); await fetchStats(); doRedirect() } catch {} finally { submitting.value = false }
}

async function generateInvite() {
  // 重新生成是**覆盖**写 user.invite_code：旧码与旧链接当场失效。
  // 以前点一下就换，提示只有「已生成」——很容易误触，把已经发给球友的码悄悄作废，
  // 对方再点链接只会看到「邀请码无效」。所以已有码时先确认。
  const hadCode = !!inviteCode.value
  if (hadCode) {
    try {
      await showConfirmDialog({
        title: '重新生成邀请码',
        message: '旧邀请码和旧邀请链接会立即失效，已经发出去的将无法再注册；已经注册成功的球友不受影响。',
        confirmButtonText: '重新生成',
        cancelButtonText: '取消',
      })
    } catch {
      return // 取消
    }
  }
  try {
    const res = await api.post('/auth/generate-invite')
    inviteCode.value = res.data.invite_code
    showToast(hadCode ? '已重新生成，旧邀请码已失效' : '已生成')
  } catch {}
}

async function copyInviteLink() {
  if (!inviteLink.value) return
  // 用统一的 copyText：navigator.clipboard 在非安全上下文（http://局域网IP）里不存在，
  // 直接调会抛 TypeError，表现为「点了没反应」
  const ok = await copyText(inviteLink.value)
  showToast(ok ? '已复制' : '复制失败，请长按选中后复制')
}

function saveGender() {
  if (!editGender.value) return
  api.put('/auth/me/profile', { gender: editGender.value }).then(() => { auth.user!.gender = editGender.value; showToast('已修改') }).catch(() => {})
}

async function savePassword() {
  if (!oldPwd.value || !newPwd.value) return
  try {
    await api.put('/auth/me/password', { old_password: oldPwd.value, new_password: newPwd.value })
    showToast('密码已修改，请重新登录')
    oldPwd.value = ''
    newPwd.value = ''
    showEditPwd.value = false
    // 改密码后旧 token 已失效，登出并回到登录页
    await auth.logout()
  } catch {}
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

async function saveEmail() {
  try {
    await api.put('/auth/me/profile', { email: editEmail.value })
    auth.user!.email = editEmail.value || null
    showToast('邮箱已更新')
  } catch {}
}

async function onRefresh() {
  try {
    await Promise.all([fetchStats(true), fetchUnread(true), auth.fetchMe(true)])
    if (auth.user?.invite_code) inviteCode.value = auth.user.invite_code
  } finally {
    refreshing.value = false
  }
}

onMounted(async () => {
  const urlParams = new URLSearchParams(window.location.search)
  const ip = urlParams.get('invite')
  if (ip) { loginTab.value = 1; registerForm.invite_code = ip }
  await Promise.all([
    api.get('/auth/has-users').then(r => { hasUsers.value = r.data.exists }).catch(() => {}),
    auth.fetchMe(),
  ])
  if (auth.user) {
    await Promise.all([fetchStats(), fetchUnread()])
    if (auth.user.invite_code) inviteCode.value = auth.user.invite_code
  }
})
</script>

<style scoped>
/* 邀请码那行的副标题只占一行：右侧的值是 8 位码/长链接，会把 label 挤得很窄，
   文字一长就折行（两行很难看）。文案已精简，这里再兜一层，窄屏宁可省略号截断 */
.invite-code-cell :deep(.van-cell__label) {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.profile-page { display: flex; flex-direction: column; background: #f5f6f8; }
.profile-scroll { flex: 1; overflow-y: auto; }
.pull-fill { min-height: 100%; }
/* 底部固定 tabbar 的占位：含系统安全区与浏览器工具栏（--tabbar-reserve 见 main.css） */
.pull-inner { padding-bottom: var(--tabbar-reserve); }
.form-scroll { flex: 1; overflow-y: auto; padding-bottom: var(--tabbar-reserve); }
.auth-tabs { margin-top: 0; }
.auth-form { margin-top: 16px; }
.form-submit { margin: 16px; }
.forgot-link { text-align: center; font-size: 13px; color: #1989fa; padding: 4px 0 20px; }
.profile-header { display: flex; flex-direction: column; align-items: center; padding: 30px 0 20px; }
.avatar-wrapper { position: relative; cursor: pointer; }
/* 头像块级化：.van-image 默认是 inline-block，行框会在图片下方留出基线间隙（实测 4px），
   于是外层容器比头像高 4px；而遮罩是 inset:0 + border-radius:50%，就跟着变成
   72×76 的椭圆 —— 圆形下缘那 4px 没有头像覆盖，看起来就是一条空白。
   用块级化而不是给容器设 line-height:0，是为了不影响容器内其它文字（如角标）的行高 */
.avatar-wrapper :deep(.van-image) { display: block; }
.avatar-overlay { position: absolute; inset: 0; border-radius: 50%; background: rgba(0,0,0,.35); display: flex; align-items: center; justify-content: center; opacity: 0; transition: opacity .2s; }
.avatar-wrapper:hover .avatar-overlay { opacity: 1; }
.profile-header h3 { margin-top: 10px; font-size: 18px; }
.logout-btn { padding: 20px 16px; }
</style>
