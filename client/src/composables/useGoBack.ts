import { useRouter } from 'vue-router'

export function useGoBack() {
  const router = useRouter()
  function goBack() {
    // 直接通过链接进入（无站内历史）时返回首页
    if (window.history.state?.back) {
      router.back()
    } else {
      router.replace('/')
    }
  }
  return { goBack }
}
