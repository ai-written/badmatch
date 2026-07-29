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
    { path: '/admin', name: 'admin', component: () => import('@/views/AdminView.vue') },
  ],
})

export default router
