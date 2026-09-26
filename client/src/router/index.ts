import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'home', component: () => import('@/views/HomeView.vue') },
    { path: '/tournament/:id', name: 'tournament', component: () => import('@/views/TournamentDetail.vue') },
    { path: '/tournament/:id/schedule', name: 'schedule', component: () => import('@/views/ScheduleView.vue') },
    { path: '/tournament/:id/rankings', name: 'rankings', component: () => import('@/views/RankingsView.vue') },
    { path: '/tournament/:id/score/:matchId', name: 'score', component: () => import('@/views/ScoreView.vue') },
    { path: '/create', name: 'create', component: () => import('@/views/CreateTournament.vue') },
    { path: '/profile', name: 'profile', component: () => import('@/views/ProfileView.vue') },
    { path: '/forgot-password', name: 'forgot-password', component: () => import('@/views/ForgotPasswordView.vue') },
    // 令牌放在 URL 的 # 之后（fragment）：不会发给服务器、不进日志与 Referer
    { path: '/reset-password', name: 'reset-password', component: () => import('@/views/ResetPasswordView.vue') },
    { path: '/admin', name: 'admin', component: () => import('@/views/AdminView.vue') },
    { path: '/notifications', name: 'notifications', component: () => import('@/views/NotificationsView.vue') },
  ],
})

export default router
