<template>
  <div class="fp-page">
    <van-nav-bar title="找回密码" left-text="返回" left-arrow @click-left="goBack" />

    <div class="fp-scroll">
      <van-form @submit="onSubmit" class="fp-form">
        <div class="fp-hint">
          输入注册时使用的邮箱，我们会发送一封带重置链接的邮件（20 分钟内有效，只能用一次）。
        </div>
        <van-cell-group inset>
          <van-field
            v-model="email"
            label="邮箱"
            type="email"
            placeholder="注册时填写的邮箱"
            required
            :rules="[{ required: true, message: '请输入邮箱' }, { pattern: /^[^@\s]+@[^@\s]+\.[^@\s]+$/, message: '邮箱格式不正确' }]"
          />
        </van-cell-group>
        <div class="fp-submit">
          <van-button round block type="primary" native-type="submit" :loading="submitting">发送重置邮件</van-button>
        </div>
      </van-form>

      <div v-if="sent" class="fp-done">
        <van-icon name="passed" size="22" color="#07c160" />
        <p>{{ sentMessage }}</p>
        <p class="fp-done-sub">没收到？请检查垃圾邮件，或稍后重新提交。若该邮箱从未在站内设置过，则无法通过邮箱找回。</p>
        <van-button size="small" round plain type="primary" @click="goBack">返回登录</van-button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import api from '@/api/client'
import { useGoBack } from '@/composables/useGoBack'

const { goBack } = useGoBack()
const email = ref('')
const submitting = ref(false)
const sent = ref(false)
const sentMessage = ref('')

async function onSubmit() {
  if (submitting.value) return
  submitting.value = true
  try {
    const res = await api.post('/auth/forgot-password', { email: email.value.trim() })
    // 服务端对「邮箱是否存在」返回同样的文案，这里原样展示即可（不额外加工，
    // 否则前端可能无意中泄露了后端刻意隐藏的信息）
    sentMessage.value = res.data?.message || '如果该邮箱已注册，我们已发送重置邮件，请查收'
    sent.value = true
  } catch {
    // 拦截器已提示（含 429 频繁提交）
  } finally {
    submitting.value = false
  }
}
</script>

<style scoped>
.fp-page { height: 100vh; display: flex; flex-direction: column; background: #f5f6f8; }
.fp-scroll { flex: 1; overflow-y: auto; }
.fp-form { padding-top: 12px; }
.fp-hint { margin: 0 16px 12px; font-size: 13px; color: #969799; line-height: 1.6; }
.fp-submit { padding: 20px 16px; }
.fp-done {
  display: flex; flex-direction: column; align-items: center; gap: 8px;
  padding: 30px 24px; text-align: center; color: #323233; font-size: 14px;
}
.fp-done p { margin: 0; }
.fp-done-sub { font-size: 12px; color: #969799; line-height: 1.6; margin-bottom: 8px !important; }
</style>
