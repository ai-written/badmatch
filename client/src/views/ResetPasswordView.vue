<template>
  <div class="rp-page">
    <van-nav-bar title="重置密码" left-text="返回" left-arrow @click-left="goBack" />

    <div class="rp-scroll">
      <div v-if="!token" class="rp-block">
        <van-icon name="warning-o" size="24" />
        <p>链接无效</p>
        <p class="rp-sub">没有找到重置凭证。请回到「找回密码」重新申请一次。</p>
        <van-button size="small" round type="primary" @click="$router.replace('/forgot-password')">
          重新申请
        </van-button>
      </div>

      <template v-else-if="!done">
        <van-form @submit="onSubmit" class="rp-form">
          <div class="rp-hint">请设置新密码。设置后所有设备都需要重新登录。</div>
          <van-cell-group inset>
            <van-field
              v-model="password"
              label="新密码"
              type="password"
              placeholder="至少 6 位"
              required
              :rules="[{ required: true, message: '请输入新密码' }, { pattern: /^.{6,}$/, message: '密码至少 6 位' }]"
            />
            <van-field
              v-model="confirm"
              label="确认密码"
              type="password"
              placeholder="再次输入新密码"
              required
              :rules="[{ required: true, message: '请再次输入新密码' }, { validator: sameAsPassword, message: '两次输入的密码不一致' }]"
            />
          </van-cell-group>
          <div class="rp-submit">
            <van-button round block type="primary" native-type="submit" :loading="submitting">确认重置</van-button>
          </div>
        </van-form>
      </template>

      <div v-else class="rp-block">
        <van-icon name="passed" size="24" color="#07c160" />
        <p>密码已重置</p>
        <p class="rp-sub">请用新密码登录。</p>
        <van-button size="small" round type="primary" @click="$router.replace('/profile')">去登录</van-button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import api from '@/api/client'
import { useGoBack } from '@/composables/useGoBack'
import { showToast } from 'vant'

const { goBack } = useGoBack()
const token = ref('')
const password = ref('')
const confirm = ref('')
const submitting = ref(false)
const done = ref(false)

function sameAsPassword(v: string) {
  return v === password.value
}

onMounted(() => {
  // 令牌放在 URL 的 # 之后（fragment）：浏览器不会把它发给服务器，
  // 因此不会进入 nginx/应用访问日志，也不会通过 Referer 泄露出去。
  const m = /(?:^|[#&])token=([^&]+)/.exec(location.hash || '')
  if (m) token.value = decodeURIComponent(m[1])
  // 读到就立刻从地址栏清掉：否则它会留在浏览器历史、截图、以及用户随后
  // 复制分享出去的链接里
  if (location.hash) {
    history.replaceState(null, '', location.pathname + location.search)
  }
})

async function onSubmit() {
  if (submitting.value) return
  submitting.value = true
  try {
    await api.post('/auth/reset-password', { token: token.value, new_password: password.value })
    done.value = true
  } catch (e: any) {
    const detail = e?.response?.data?.detail
    // 令牌无效/过期时给出可操作的下一步，而不是只有一句报错
    if (e?.response?.status === 400) {
      showToast(detail || '重置链接无效或已过期')
    }
  } finally {
    submitting.value = false
  }
}
</script>

<style scoped>
.rp-page { height: 100vh; display: flex; flex-direction: column; background: #f5f6f8; }
.rp-scroll { flex: 1; overflow-y: auto; }
.rp-form { padding-top: 12px; }
.rp-hint { margin: 0 16px 12px; font-size: 13px; color: #969799; line-height: 1.6; }
.rp-submit { padding: 20px 16px; }
.rp-block {
  display: flex; flex-direction: column; align-items: center; gap: 8px;
  padding: 80px 24px 0; text-align: center; color: #323233; font-size: 14px;
}
.rp-block p { margin: 0; }
.rp-sub { font-size: 12px; color: #969799; line-height: 1.6; margin-bottom: 8px !important; }
</style>
