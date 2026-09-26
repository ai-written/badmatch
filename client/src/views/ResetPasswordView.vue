<template>
  <div class="rp-page">
    <van-nav-bar title="重置密码" left-text="返回" left-arrow @click-left="goBack" />

    <div class="rp-scroll">
      <div v-if="!token || tokenDead" class="rp-block">
        <van-icon name="warning-o" size="24" />
        <p>{{ tokenDead ? '链接已失效' : '链接无效' }}</p>
        <p class="rp-sub">{{ deadReason || '没有找到重置凭证。' }}请重新申请一次。</p>
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
              placeholder="6 位以上"
              maxlength="30"
              required
              :rules="[{ required: true, message: '请输入新密码' }, { pattern: /^.{6,}$/, message: '密码至少 6 位' }]"
            />
            <van-field
              v-model="confirm"
              label="确认密码"
              type="password"
              placeholder="再次输入新密码"
              maxlength="30"
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
// 令牌被服务端判为无效/过期：此时留在表单上没有意义（再点还是失败），
// 直接切到「重新申请」出口
const tokenDead = ref(false)
const deadReason = ref('')

function sameAsPassword(v: string) {
  return v === password.value
}

onMounted(() => {
  // 令牌放在 URL 的 # 之后（fragment）：浏览器不会把它发给服务器，
  // 因此不会进入 nginx/应用访问日志，也不会通过 Referer 泄露出去。
  const m = /(?:^|[#&])token=([^&]+)/.exec(location.hash || '')
  if (m) {
    try {
      token.value = decodeURIComponent(m[1])
    } catch {
      // 邮件网关改写链接等导致的畸形百分号转义：原样用，让服务端去判无效，
      // 不要在这里抛 URIError（会跳过后面的地址栏清理）
      token.value = m[1]
    }
  }
  if (location.hash) {
    // 读到就立刻从地址栏清掉：否则它会留在浏览器历史、截图、以及用户随后
    // 复制分享出去的链接里。
    // 传 history.state 而不是 null：vue-router 依赖它记录 back/forward 位置，
    // 传 null 会让本页的「返回」退化成回首页。
    history.replaceState(history.state, '', location.pathname + location.search)
  }
})

async function onSubmit() {
  if (submitting.value) return
  submitting.value = true
  try {
    // skipGlobalError：错误文案由本页决定（拦截器再弹一次会重复提示）
    await api.post(
      '/auth/reset-password',
      { token: token.value, new_password: password.value },
      { skipGlobalError: true } as any,
    )
    done.value = true
  } catch (e: any) {
    const status = e?.response?.status
    const detail = e?.response?.data?.detail
    if (status === 400) {
      // 令牌无效/过期：切到可恢复的状态，而不是让用户对着死表单反复点
      tokenDead.value = true
      deadReason.value = (typeof detail === 'string' && detail) || ''
    } else if (status === 429) {
      showToast('操作过于频繁，请稍后再试')
    } else if (status === 422) {
      showToast('密码格式不符合要求')
    } else {
      showToast('网络异常，请稍后重试')
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
