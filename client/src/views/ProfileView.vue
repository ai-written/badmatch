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
      <AppHero :height="200">
        <div class="me">
          <div class="avatar-wrapper" @click="triggerUpload">
            <van-image lazy-load round width="56" height="56" :src="auth.user.avatar || defaultAvatar" />
            <div class="avatar-overlay"><van-icon name="photograph" size="18" color="#fff" /></div>
          </div>
          <div class="me-txt">
            <div class="hero-title me-name">{{ auth.user.username }}</div>
            <div class="hero-sub me-sub">爱玩羽社 · {{ joinedText }}</div>
          </div>
        </div>
        <div class="me-stats">
          <div><em class="ui-num">{{ stats.total_matches }}</em><span>总场次</span></div>
          <div><em class="ui-num">{{ stats.win_rate }}%</em><span>胜率</span></div>
          <div><em class="ui-num">{{ stats.tournaments_played }}</em><span>参赛次数</span></div>
        </div>
        <input ref="fileInput" type="file" accept="image/*" style="display:none" @change="onFileChange" />
      </AppHero>

      <div class="ui-sheet">
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
        <van-cell
          title="参赛次数" :value="String(stats.tournaments_played)"
          :is-link="stats.tournaments_played > 0"
          :clickable="stats.tournaments_played > 0"
          @click="openHistory"
        >
          <template v-if="stats.tournaments_played > 0" #label>点击看排名趋势</template>
        </van-cell>
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

    <!-- 我的参赛排名趋势（点「参赛次数」打开）：与赛事详情里点头像看到的是同一张图 -->
    <van-popup v-model:show="showHistory" round position="bottom" class="vh-sheet vh-65" lock-scroll>
      <div class="picker-toolbar">
        <span @click="showHistory = false">关闭</span>
        <span class="picker-title">我的参赛名次</span>
      </div>
      <div class="vh-sheet-body history-body">
        <van-loading v-if="historyLoading" class="history-loading" />
        <div v-else-if="historyFailed" class="history-failed" @click="loadHistory(true)">
          加载失败，点这里重试
        </div>
        <RankHistoryChart v-else :items="historyItems" />
      </div>
    </van-popup>
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
import RankHistoryChart from '@/components/RankHistoryChart.vue'
import AppHero from '@/components/AppHero.vue'

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
/** 加入天数（用 /auth/me 的 created_at；取不到就显示"—"）。
 *
 *  注册当天算 0 天，显示「今天加入」而不是「已加入 0 天」（后者读着像没加进来）；
 *  原来这里用 Math.max(1, …) 把当天硬抬成 1 天，反而把"今天刚注册"说成"已加入 1 天"。
 *  注：后端 /auth/me 以前压根没返回 created_at，所以这段永远走"—"分支（已修）。
 */
const joinedText = computed(() => {
  const raw = auth.user?.created_at
  if (!raw) return '已加入 — 天'
  const t = new Date(raw).getTime()
  if (!t || isNaN(t)) return '已加入 — 天'
  const days = Math.floor((Date.now() - t) / 86400000)
  if (days <= 0) return '今天加入'
  return `已加入 ${days} 天`
})

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

/* ---------------- 我的参赛名次趋势 ----------------
 * 点「参赛次数」才拉（这个接口要扫他所有赛事的战绩，不必每次进个人页都拉）。
 * 数据口径与赛事详情里点头像看别人完全一样，共享同一个组件。
 */
const showHistory = ref(false)
const historyItems = ref<any[]>([])
const historyLoaded = ref(false)
const historyUid = ref<number | null>(null)
const historyLoading = ref(false)
const historyFailed = ref(false)

async function loadHistory(force = false) {
  const uid = auth.user?.id
  if (!uid) return
  // 缓存跟着**人**走：退出登录后换个账号进来，historyLoaded 还是 true，
  // 若只判断这个标志就会把上一个账号的折线当成自己的
  if (historyLoaded.value && historyUid.value === uid && !force) return
  historyLoading.value = !historyItems.value.length
  historyFailed.value = false
  try {
    const res = await api.get(`/auth/stats/${uid}/history`, { skipLoading: true } as any)
    if (auth.user?.id !== uid) return
    historyItems.value = res.data?.items || []
    historyLoaded.value = true
    historyUid.value = uid
  } catch {
    if (auth.user?.id !== uid) return
    // 已有旧数据时不切失败态：旧图比错误提示有用
    if (!historyItems.value.length) historyFailed.value = true
  } finally {
    if (auth.user?.id === uid) historyLoading.value = false
  }
}

function openHistory() {
  if (!(stats.tournaments_played > 0)) return
  showHistory.value = true
  loadHistory()
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
    // 下拉刷新是"我要最新数据"的手势：趋势图的缓存也一并作废，
    // 否则「参赛次数」涨了、点开还是旧的折线（下次打开时重拉）
    historyLoaded.value = false
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
   文字一长就折行（两行很难看）。文案已精简，这里再兜一层，窄屏宁可省略号截断。
   注意 min-width:0 不能省：Vant 的 .van-cell__title 是 flex 项，默认 min-width:auto，
   nowrap 会把它的最小宽度顶成整行文字宽度 —— 少了这条，省略号永远不触发，
   被挤的反而是右侧的值（与本意相反）。 */
.invite-code-cell :deep(.van-cell__title) { min-width: 0; }
.invite-code-cell :deep(.van-cell__label) {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.profile-page { display: flex; flex-direction: column; }
.profile-scroll { flex: 1; overflow-y: auto; }
.pull-fill { min-height: 100%; }
/* 底部固定 tabbar 的占位：含系统安全区与浏览器工具栏（--tabbar-reserve 见 main.css） */
.pull-inner { padding-bottom: var(--tabbar-reserve); }
.pull-inner :deep(.van-cell-group--inset) {
  margin: 0 0 12px; border-radius: 18px; overflow: hidden;
  box-shadow: 0 4px 14px rgba(17, 45, 90, .07); background: #fff;
}
.form-scroll { flex: 1; overflow-y: auto; padding-bottom: var(--tabbar-reserve); }
.auth-tabs { margin-top: 0; }
.auth-form { margin-top: 16px; }
.form-submit { margin: 16px; }
.forgot-link { text-align: center; font-size: 13px; color: #1989fa; padding: 4px 0 20px; }
/* ---- 头图：头像 + 名字 + 三格战绩 ---- */
.me { display: flex; align-items: center; gap: 13px; padding-top: 10px; }
.me-txt { flex: 1; min-width: 0; }
.me-name { font-size: 19px; font-weight: 800; letter-spacing: .2px; }
.me-sub { font-size: 11.5px; margin-top: 5px; }
.me-stats { display: flex; gap: 9px; margin-top: 16px; }
.me-stats > div {
  flex: 1; background: rgba(255, 255, 255, .16);
  border: 1px solid rgba(255, 255, 255, .22); border-radius: 13px;
  padding: 8px 10px; text-align: center;
}
.me-stats em { font-style: normal; font-size: 18px; font-weight: 800; }
.me-stats span { display: block; font-size: 10.5px; opacity: .9; margin-top: 2px; }
.avatar-wrapper {
  position: relative; cursor: pointer; flex-shrink: 0;
  border-radius: 50%; border: 2px solid rgba(255, 255, 255, .8);
  overflow: hidden; box-shadow: 0 3px 10px rgba(9, 52, 110, .22);
}
/* 头像块级化：.van-image 默认是 inline-block，行框会在图片下方留出基线间隙（实测 4px），
   于是外层容器比头像高 4px；而遮罩是 inset:0 + border-radius:50%，就跟着变成
   72×76 的椭圆 —— 圆形下缘那 4px 没有头像覆盖，看起来就是一条空白。
   用块级化而不是给容器设 line-height:0，是为了不影响容器内其它文字（如角标）的行高 */
.avatar-wrapper :deep(.van-image) { display: block; }
.avatar-overlay { position: absolute; inset: 0; border-radius: 50%; background: rgba(0,0,0,.35); display: flex; align-items: center; justify-content: center; opacity: 0; transition: opacity .2s; }
.avatar-wrapper:hover .avatar-overlay { opacity: 1; }
.profile-header h3 { margin-top: 10px; font-size: 18px; }
.logout-btn { padding: 20px 16px; }

/* 我的参赛名次趋势弹层 */
.picker-toolbar {
  display: flex; align-items: center; justify-content: space-between;
  padding: 12px 16px; font-size: 14px; color: #1989fa; flex-shrink: 0;
}
.picker-title { color: #323233; font-weight: 600; font-size: 15px; }
.history-body { padding: 0 16px 16px; }
.history-loading { display: flex; justify-content: center; padding: 40px 0; }
.history-failed { padding: 40px 0; text-align: center; color: #969799; font-size: 13px; }
</style>
