<template>
  <div class="home-page vh-page">
    <div class="home-scroll">
      <van-pull-refresh v-model="refreshing" @refresh="onRefresh" class="pull-fill">
        <div class="pull-inner">
          <!-- 头图跟着内容一起滚（与设计稿一致）：下拉刷新手势覆盖整屏 -->
          <AppHero :height="156">
            <div class="brand">
              <img src="/favicon.png" class="brand-logo" alt="" />
              <div class="brand-txt">
                <div class="hero-title">爱玩羽社</div>
                <div class="hero-sub">以球会友 · 快乐运动</div>
              </div>
              <button class="brand-add" aria-label="创建赛事" @click="$router.push('/create')">＋</button>
            </div>
          </AppHero>

          <div class="home-sheet">
            <van-list
              v-model:loading="loading"
              :finished="finished"
              :error="loadError"
              error-text="加载失败，点击重试"
              finished-text="没有更多了"
              @update:error="loadError = false"
              @load="onLoad"
            >
              <div
                v-for="(t, i) in sortedList"
                :key="t.id"
                class="t-card"
                :class="statusKey(t)"
                @click="$router.push(`/tournament/${t.id}`)"
              >
                <!-- 左：日期块（日期只在这里出现一次，不再和时间行重复） -->
                <div class="t-date" :class="`tile-${i % 3}`">
                  <b>{{ dayLabel(t.start_date) }}</b>
                  <i>{{ weekLabel(t.start_date) }}</i>
                  <span class="t-bar" />
                </div>
                <div class="t-main">
                  <div class="t-title">{{ t.title }}</div>
                  <div class="t-meta">
                    <van-icon name="location-o" size="12" />
                    <span class="t-loc">{{ t.location || '待定' }}</span>
                    <span v-if="t.court_name" class="t-court">{{ t.court_name }}</span>
                  </div>
                  <div class="t-meta time">
                    <van-icon name="clock-o" size="12" />
                    <span>{{ timeLabel(t) }}</span>
                  </div>
                </div>
                <div class="t-side">
                  <span class="t-st">{{ statusLabel(t) }}</span>
                  <span class="t-count">{{ countLabel(t) }}</span>
                </div>
              </div>

              <!-- 首屏就失败时给一个明确的失败态：不要把空列表说成「没有更多了」 -->
              <div v-if="list.length === 0 && firstLoadFailed" class="home-error">
                <van-icon name="warning-o" size="20" />
                <span>加载失败，请下拉重试</span>
              </div>
            </van-list>
          </div>
        </div>
      </van-pull-refresh>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useTournamentStore } from '@/stores/tournament'
import AppHero from '@/components/AppHero.vue'

const store = useTournamentStore()
// 解构 store 的 state 会丢响应式，因此列表用本地 ref 镜像；
// loading 则完全由 van-list 通过 v-model:loading 接管：
// 它在 check() 里置为 true，并要求 @load 处理函数结束时置回 false，
// 否则内部 loading 一直为 true，之后不会再触发加载（分页失效）。
// 注意不能用 ref(store.xxx) 去「共享」另一个 ref —— 那只是取值，不是同一个引用。
const list = ref(store.list)
const loading = ref(false)
const refreshing = ref(false)
const finished = ref(false)
// van-list 的错误态：置位后停止自动加载，点错误文案可重试
const loadError = ref(false)
const firstLoadFailed = ref(false)
// 是否已经发起过首次加载（同步标记，见 onLoad 的说明）
let firstLoadDone = false

/* ---------------- 排序：进行中 → 报名中 → 已结束 ----------------
 * 为什么前端排：首页原先按 created_at 倒序，打开就是一片历史记录，最该看的
 * "下一场能不能报名"被压到下面（截图里一屏 7 条全是已结束）。
 *
 * 口径：
 * - 进行中排最前；报名中按**开赛时间升序**（最近要打的那场在最上面）；
 * - 已结束按**开赛时间降序**（刚打完的在前，越翻越旧）。
 * - 只在**已加载的列表内**排序：接口是分页的（按 created_at 分页），前端没法
 *   假装全局有序。俱乐部一周一场，首屏 20 条通常就覆盖了，够用；
 *   真要全局有序得给接口加 sort 参数（后端改动，另说）。
 * - 同一天多场时用 id 兜底，避免刷新后顺序抖动。
 */
const GROUP = { ongoing: 0, open: 1, finished: 2 } as const
function groupOf(t: any) { return GROUP[t.status as keyof typeof GROUP] ?? 3 }
const sortedList = computed(() => {
  const arr = [...list.value]
  arr.sort((a: any, b: any) => {
    const g = groupOf(a) - groupOf(b)
    if (g !== 0) return g
    const da = String(a.start_date || '')
    const db = String(b.start_date || '')
    if (da !== db) return a.status === 'finished' ? (da < db ? 1 : -1) : (da < db ? -1 : 1)
    return a.id - b.id
  })
  return arr
})

function statusKey(s: any) {
  // 已结束=中性灰；进行中=绿；报名中/未开始=品牌蓝（未开始再用橙色区分）
  if (s.status === 'finished') return 'done'
  if (s.status === 'ongoing') return 'live'
  return isRegLocked(s) ? 'soon' : 'open'
}
// 列表上的「未开始」要随时间自己翻转：只在渲染时算一次的话，开放时刻到了之后卡片会
// 一直停在「未开始」，直到用户下拉刷新。每 30s 触发一次重算（纯本地比较，不发请求）。
// 注意：列表接口不返回 server_now，这里用的是手机时钟（偏差通常几秒，无感）；
// 详情页的倒计时用服务端时间校准过，那里才是权威显示。
const nowTick = ref(Date.now())
let tickTimer: ReturnType<typeof setInterval> | null = null
function isRegLocked(t: any) { return !!t.registration_open_at && new Date(t.registration_open_at).getTime() > nowTick.value }
function statusLabel(t: any) { return t.status === 'open' ? (isRegLocked(t) ? '未开始' : '报名中') : t.status === 'ongoing' ? '进行中' : '已结束' }
onMounted(() => { tickTimer = setInterval(() => { nowTick.value = Date.now() }, 30000) })
onUnmounted(() => { if (tickTimer) clearInterval(tickTimer) })

const WEEKDAYS = ['周日', '周一', '周二', '周三', '周四', '周五', '周六']
function parse(v: string) { return v ? new Date(v) : null }
/** 日期块的「11月6日」 */
function dayLabel(start: string) {
  const d = parse(start)
  return d && !isNaN(d.getTime()) ? `${d.getMonth() + 1}月${d.getDate()}日` : ''
}
/** 日期块的「周五」 */
function weekLabel(start: string) {
  const d = parse(start)
  return d && !isNaN(d.getTime()) ? WEEKDAYS[d.getDay()] : ''
}
/**
 * 时间行：只写时段，不再重复日期（标题里通常已含 "11月6日"）。
 * 报名中的再补一句剩余名额——这是用户真正关心的决策信息。
 */
function timeLabel(t: any) {
  const a = String(t.start_date || '').slice(11, 16)
  const b = String(t.end_date || '').slice(11, 16)
  if (!a) return ''
  const span = b ? `${a}–${b}` : a
  if (t.status === 'open' && !isRegLocked(t)) {
    const left = Math.max(0, (t.max_participants || 0) - (t.registered_count || 0))
    return left > 0 ? `${span} · 剩 ${left} 席` : `${span} · 已满`
  }
  return span
}
/** 右侧人数：报名中/进行中给 "6/8 人"，已结束给 "8 人"（分母对已结束没意义） */
function countLabel(t: any) {
  const n = t.registered_count || 0
  if (t.status === 'finished') return `${n} 人`
  return `${n}/${t.max_participants || 0} 人`
}

async function onLoad() {
  // van-list 在挂载阶段会连续触发多次 @load：初始 check 一次，之后每次
  // loading/finished/error 变化又会重新 check（见 vant 的
  // watch(() => [props.loading, props.finished, props.error], check)）。
  // 这些触发都在首个请求完成【之前】，所以不能用「请求完成后的状态」判断首次，
  // 也不能让它们并发 —— 否则会各自请求一次、page 被重复自增。
  //
  // 处理办法：
  //   1) 用同步标记判断首次（第一次调用进入时立刻置位）
  //   2) 请求在途时直接返回；van-list 会在 loading 复位后再 check，届时会重新触发
  const reset = !firstLoadDone
  if (!reset && store.isBusy()) return
  firstLoadDone = true
  try {
    await store.fetchList(undefined, reset, !reset)
    list.value = store.list
    firstLoadFailed.value = false
    // 拉完一页后按「是否还有更多」收尾；注意不能在上面的 fetch 之前就置 finished ——
    // 那会让 van-list 的转圈条件（loading && !finished）永远不成立，首屏没有加载提示
    finished.value = !store.hasMore
  } catch {
    if (reset) {
      firstLoadFailed.value = true
      finished.value = true   // 停止自动加载，改为展示失败态 / 下拉重试
    } else {
      loadError.value = true  // 追加失败：保留已加载内容，点错误文案可重试
    }
  } finally {
    // 必须置回 false（会通过 v-model:loading 通知 van-list 本次加载结束）
    loading.value = false
  }
}

async function onRefresh() {
  try {
    store.reset()
    await store.fetchList(undefined, true, true)
    list.value = store.list
    finished.value = !store.hasMore
    firstLoadFailed.value = false
  } catch {
    firstLoadFailed.value = true
    finished.value = true
  } finally {
    loadError.value = false
    // 注意：这里不要再动 loading。它是 van-list 通过 v-model:loading 管理的，
    // 而 vant 会 watch loading 的变化再次 check()；在刷新请求结束时手动置 false
    // 会在「追加请求仍在途」时解锁 van-list，导致同一页被追加两次。
    refreshing.value = false
  }
}

</script>

<style scoped>
.home-page { display: flex; flex-direction: column; background: #f6faff; }
.home-scroll { flex: 1; overflow-y: auto; }
.pull-fill { min-height: 100%; }
/* 底部固定 tabbar 的占位：含系统安全区与浏览器工具栏（--tabbar-reserve 见 main.css） */
.pull-inner { padding-bottom: var(--tabbar-reserve); }

/* ---- 头图内容 ---- */
.brand { display: flex; align-items: center; gap: 11px; padding-top: 6px; }
.brand-logo {
  width: 40px; height: 40px; border-radius: 14px; flex-shrink: 0;
  border: 1.5px solid rgba(255, 255, 255, .8);
  box-shadow: 0 3px 8px rgba(9, 52, 110, .22);
  background: #fff;
}
.brand-txt { flex: 1; min-width: 0; }
.hero-title { font-size: 19px; font-weight: 800; letter-spacing: .3px; }
.hero-sub { font-size: 11px; opacity: .95; margin-top: 4px; letter-spacing: .8px; }
.brand-add {
  width: 34px; height: 34px; border-radius: 50%; flex-shrink: 0;
  border: 1.5px solid rgba(255, 255, 255, .75);
  background: rgba(255, 255, 255, .16);
  color: #fff; font-size: 19px; line-height: 1;
  display: flex; align-items: center; justify-content: center;
  cursor: pointer; padding: 0;
}
.brand-add:active { background: rgba(255, 255, 255, .3); }

/* ---- 内容浮层：压住头图底部圆角 ---- */
.home-sheet {
  position: relative;
  /* 负 margin 把头图的波浪盖住一截，圆角够大才有"卡片浮起"的感觉 */
  margin-top: -28px; border-radius: 28px 28px 0 0;
  /* 很浅的蓝白（不是纯白）：白卡要靠"底色微蓝 + 阴影"浮起来，
     纯白底 + 描边会把层次压平，和设计稿差很远 */
  background: linear-gradient(180deg, #f8fbff 0%, #f2f7fe 100%);
  padding: 16px 12px 0; min-height: calc(100vh - 150px);
  /* 压在图上的那道边：一条极淡的亮边 + 一点向上的阴影，接缝才不会糊成一条直线 */
  box-shadow: 0 -10px 24px rgba(9, 52, 110, .12);
  border-top: 1.5px solid rgba(255, 255, 255, .8);
}

/* ---- 赛事卡 ---- */
.t-card {
  display: flex; align-items: stretch; gap: 11px;
  background: #fff; border-radius: 18px; padding: 14px;
  box-shadow: 0 4px 14px rgba(17, 45, 90, .07);
  margin-bottom: 12px; cursor: pointer;
}
.t-card:active { background: #fafcff; }

/* 左：日期块 = 设计稿里的羽毛球贴片（紫/橙/薄荷三色轮换，带圆角透明边）+ 代码渲染的日期。
   贴片是从设计稿大图裁的（client/public/tile/tile-*.webp，263px ≈ 3.2x@82），
   图里没有文字 —— 日期/星期都是真 DOM，可选中、可改、放大不糊。
   构图：贴片锚在右下角、略微出框，左上留干净区域放文字（贴片本身的羽毛球也在右下部）。 */
.t-date {
  position: relative; overflow: hidden;
  width: 104px; flex-shrink: 0;
  border-radius: 16px; padding: 9px 8px 10px;
  display: flex; flex-direction: column; align-items: flex-start; justify-content: flex-start;
  background-repeat: no-repeat;
  box-shadow: 0 1px 3px rgba(17, 45, 90, .05);
  min-height: 76px;
}
.t-date.tile-0 { background-color: #faf9ff; background-image: url(/tile/tile-purple.jpg?v=4); background-size: cover; background-position: center; }
.t-date.tile-1 { background-color: #fdefe4; background-image: url(/tile/tile-orange.jpg?v=4); background-size: cover; background-position: center; }
.t-date.tile-2 { background-color: #f7fdf9; background-image: url(/tile/tile-mint.jpg?v=4); background-size: cover; background-position: center; }
.t-date > * { position: relative; z-index: 1; }

.t-date b {
  font-size: 14px; font-weight: 800; white-space: nowrap; letter-spacing: -.3px;
  text-shadow: 0 0 6px rgba(255, 255, 255, .95), 0 1px 2px rgba(255, 255, 255, .95);
}
.t-date i {
  font-style: normal; font-size: 11px; color: #57606f; margin-top: 3px;
  text-shadow: 0 0 6px rgba(255, 255, 255, .95);
}
/* 状态色下划线：报名中蓝 / 进行中绿 / 已结束灰 */
.t-bar { width: 20px; height: 3px; border-radius: 2px; margin-top: 7px; background: #1677e0; }
.t-card.done .t-date b { color: #5b626d; }
.t-card.done .t-bar { background: #cfd6e0; }
.t-card.live .t-bar { background: #0aa054; }
.t-card.soon .t-bar { background: #e8790b; }
.t-main { flex: 1; min-width: 0; padding-left: 12px; border-left: 1px solid #eef3fa; }
.t-row { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.t-title {
  font-size: 16.5px; font-weight: 700; letter-spacing: -.2px;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.t-card.done .t-title { color: #5b626d; font-weight: 600; }
.t-st {
  font-size: 10px; font-weight: 800; padding: 2px 7px; border-radius: 7px;
  flex-shrink: 0; letter-spacing: .2px;
  background: #e8f3ff; color: #1677e0;
}
.t-card.done .t-st { background: #f2f4f7; color: #98a1ad; }
.t-card.live .t-st { background: #eaf9f0; color: #0aa054; }
.t-card.soon .t-st { background: #fff2e4; color: #e8790b; }

.t-meta {
  display: flex; align-items: center; gap: 5px;
  font-size: 12.5px; color: #7c8698; margin-top: 7px;
  min-width: 0;
}
.t-meta.time { font-variant-numeric: tabular-nums; }
.t-loc { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.t-court {
  font-size: 10.5px; font-weight: 600; color: #1677e0; background: #eaf4ff;
  border-radius: 6px; padding: 1px 6px; flex-shrink: 0;
}
.t-card.done .t-court { color: #8a94a6; background: #f2f4f7; }

/* 右侧一列：状态胶囊在上、人数在下（和设计稿一致）。
   之前把胶囊塞进标题那一行，长标题被挤成"多场地测试 · 12人2…" */
.t-side {
  display: flex; flex-direction: column; align-items: flex-end; gap: 8px;
  flex-shrink: 0; padding-left: 4px;
}
.t-count {
  font-size: 14px; font-weight: 800; color: #46505f;
  font-variant-numeric: tabular-nums; white-space: nowrap;
}
.t-card.done .t-count { color: #98a1ad; }

.home-error {
  display: flex; flex-direction: column; align-items: center; gap: 6px;
  padding: 60px 0; color: #969799; font-size: 13px;
}
</style>
