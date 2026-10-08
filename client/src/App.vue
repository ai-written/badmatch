<template>
  <div class="app-root">
    <!-- 用 path 作 key：同一个组件被不同参数复用时（例如将来出现"从对阵表直接跳到另一个
         赛事的对阵表"），必须重建组件实例。否则页内那些"按组件实例一次性求值"的状态
         （赛事 id、筛选条件 key、WebSocket 频道、已加载的赛程）会串到新赛事上。
         按 path 而不是 fullPath：只改 query（如记分页的 ?num=）不该重建。 -->
    <router-view :key="route.path" />
  </div>
  <van-tabbar v-if="showTabbar" route active-color="#1989fa" inactive-color="#999" safe-area-inset-bottom>
    <van-tabbar-item icon="orders-o" to="/">赛事</van-tabbar-item>
    <van-tabbar-item icon="user-o" to="/profile">我的</van-tabbar-item>
  </van-tabbar>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const route = useRoute()
const auth = useAuthStore()
// 未登录时不显示 tabbar：现在除积分榜外都要登录，匿名访客只可能停在 /profile
// （登录页）——此时「赛事」这个 tab 点下去会被守卫弹回来，看着像坏了。
// 用 store 里的 token（响应式，登出后立即消失）而不是直接读 localStorage。
const showTabbar = computed(
  () => (route.path === '/' || route.path === '/profile') && !!auth.token
)
</script>

<style>
.app-root { }
</style>
