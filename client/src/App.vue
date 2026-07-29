<template>
  <div class="app-root">
    <router-view />
  </div>
  <van-tabbar v-if="showTabbar" route active-color="#1989fa" inactive-color="#999" safe-area-inset-bottom>
    <van-tabbar-item icon="orders-o" to="/">赛事</van-tabbar-item>
    <van-tabbar-item icon="user-o" to="/profile">我的</van-tabbar-item>
  </van-tabbar>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'

const route = useRoute()
const showTabbar = computed(() => route.path === '/' || route.path === '/profile')
</script>

<style>
.app-root { }
</style>
