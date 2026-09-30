import { createRouter, createWebHistory } from 'vue-router'

/**
 * 访问策略：除标注 `meta.public` 的四处入口外，所有页面都要求登录。
 *
 * 这里只是"把人引到登录页"的体验层——真正的边界在后端：除积分榜外，
 * 赛事列表/详情/报名名单/对阵/单场/战绩这些读取接口都要求登录（见 server/app/api/），
 * 只靠前端守卫挡不住直接调接口的人。
 *
 * 保留匿名的四处，少一个就会死锁或功能不可用：
 * - /profile                 登录注册页，也是邀请链接的落地页（?invite=邀请码）
 * - /forgot-password         忘记密码入口（走到这里时必然没有登录态）
 * - /reset-password          邮件里的重置链接，令牌在 # 里，打开时本来就没登录态
 * - /tournament/:id/rankings 积分榜：只读、无隐私，是这个封闭群唯一对外的展示面
 *                            （分享图也是从这里生成的），刻意留公开
 */
const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'home', component: () => import('@/views/HomeView.vue') },
    { path: '/tournament/:id', name: 'tournament', component: () => import('@/views/TournamentDetail.vue') },
    { path: '/tournament/:id/schedule', name: 'schedule', component: () => import('@/views/ScheduleView.vue') },
    { path: '/tournament/:id/rankings', name: 'rankings', component: () => import('@/views/RankingsView.vue'), meta: { public: true } },
    { path: '/tournament/:id/score/:matchId', name: 'score', component: () => import('@/views/ScoreView.vue') },
    { path: '/create', name: 'create', component: () => import('@/views/CreateTournament.vue') },
    { path: '/profile', name: 'profile', component: () => import('@/views/ProfileView.vue'), meta: { public: true } },
    { path: '/forgot-password', name: 'forgot-password', component: () => import('@/views/ForgotPasswordView.vue'), meta: { public: true } },
    // 令牌放在 URL 的 # 之后（fragment）：不会发给服务器、不进日志与 Referer
    { path: '/reset-password', name: 'reset-password', component: () => import('@/views/ResetPasswordView.vue'), meta: { public: true } },
    { path: '/admin', name: 'admin', component: () => import('@/views/AdminView.vue') },
    { path: '/notifications', name: 'notifications', component: () => import('@/views/NotificationsView.vue') },
    // 兜底：手打错的地址（或旧链接）不再白屏。redirect 在路由解析阶段就会跟随，
    // 所以 beforeEach 收到的是最终目标 '/'——未登录会被守卫带去登录页、
    // 登录后回到首页；已登录则直接进首页。
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})

router.beforeEach((to) => {
  if (to.meta.public) return true
  // 只看有没有 token：是否仍然有效交给接口判定（401 时 axios 拦截器会清掉 token 并按
  // 同一套 loginRedirect 逻辑跳回登录页），这样每次跳转不必先多打一个 /auth/me
  if (localStorage.getItem('token')) return true
  // 记下原目标，登录成功后 ProfileView 会自动回跳（与 401 处理共用同一个 key）
  sessionStorage.setItem('loginRedirect', to.fullPath)
  // 目标里带邀请码时要一并带到登录页：报名海报的二维码就是 /tournament/16?invite=xxx，
  // 而 ProfileView 是从 ?invite= 预填注册表单的 —— 少了这一步，扫码进来的新用户
  // 会落到一个空白的注册表单前，只能回去找人要邀请码。
  const invite = to.query.invite
  return { path: '/profile', query: invite ? { invite: String(invite) } : {}, replace: true }
})

export default router
