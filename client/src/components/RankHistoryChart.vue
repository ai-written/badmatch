<!--
  参赛排名趋势折线图（手写 SVG，不引图表库）。

  为什么不用 echarts/chart.js：这里最多二十几个点、只有一条线，
  引一个 300KB+ 的图表库（还要处理按需引入和 chunk 分包）不划算，
  而且配色/字号要跟 Vant 统一，手写反而更好调。点位换算、平滑曲线、
  汇总统计全在 utils/rankHistory.ts（有单测），这里只负责画。

  几个刻意的取舍：
  - 纵轴是**相对位置**（第 1 名在顶、最后一名在底），不是绝对名次 ——
    各场人数 4~64 不等，绝对刻度会把"8 人第 3"和"24 人第 3"画成一样高。
    每个点旁边标"3/8"，真实名次和人数都不丢；中间那条虚线就是"中位"。
  - 折线用**单调**三次插值（Fritsch–Carlson）而不是普通圆角平滑：后者会在
    两点之间拱出比两端更高的弧，看着像"那周更好"，而数据里没有那个值。
  - 退赛点（rank=0）画在底部灰带里、不连线：连起来会伪造出一段
    "从第 2 名掉到最后"的假趋势，而且它本来就不占名次。
  - 没结束的赛事用空心点 + 虚线：名次还会变，不能和已定型的点画成一样。
  - 窄屏塞不下十几个日期标签，横轴只标首尾（其余靠点击看详情）。
-->
<template>
  <div class="rank-chart">
    <div class="chart-head">
      <span class="chart-title">参赛排名趋势</span>
      <span class="chart-sub">{{ headSub }}</span>
    </div>

    <van-empty v-if="!total" image="search" description="暂无参赛记录" />

    <template v-else>
      <!-- 图上方的小结：最好 / 最差 / 平均位置。纯数字一眼看懂，比只看折线省事 -->
      <div v-if="sum.ranked" class="metrics">
        <div class="metric">
          <span class="metric-value best">{{ sum.best?.rankLabel || '-' }}</span>
          <span class="metric-label">最好名次</span>
        </div>
        <div class="metric">
          <span class="metric-value">{{ sum.worst?.rankLabel || '-' }}</span>
          <span class="metric-label">最差名次</span>
        </div>
        <div class="metric">
          <span class="metric-value">{{ avgText }}</span>
          <span class="metric-label">平均位置</span>
        </div>
      </div>

      <div class="chart-card">
        <svg
          class="chart-svg"
          :viewBox="`0 0 ${W} ${H}`"
          preserveAspectRatio="xMidYMid meet"
          role="img"
          :aria-label="`参赛排名趋势：共 ${total} 场`"
          @click="onSvgClick"
        >
          <defs>
            <linearGradient :id="lineGradId" x1="0" y1="0" x2="1" y2="0">
              <stop offset="0%" stop-color="#5cb0ff" />
              <stop offset="55%" stop-color="#1989fa" />
              <stop offset="100%" stop-color="#0b6fd0" />
            </linearGradient>
            <linearGradient :id="areaGradId" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stop-color="#1989fa" stop-opacity="0.22" />
              <stop offset="100%" stop-color="#1989fa" stop-opacity="0" />
            </linearGradient>
          </defs>

          <!-- 退赛条带：放在**图区下方**（掉出排名区），不压在折线上。
               含义由图例说明，条带上不再写字 —— 每条都写会和日期、点名互相压 -->
          <rect
            v-if="hasUnranked" class="band"
            :x="PAD.left" :y="bandY - 6.5" :width="plotW" height="13" rx="6.5"
          />

          <!-- 网格：只有顶/中/底三条；中间那条是"中位线" -->
          <line
            v-for="t in Y_TICKS" :key="`g${t}`"
            :x1="PAD.left" :x2="W - PAD.right"
            :y1="yAt(t)" :y2="yAt(t)"
            class="grid" :class="{ base: t === 0 || t === 1, mid: t === 0.5 }"
          />
          <text class="axis-label" :x="PAD.left - 6" :y="yAt(0) + 3.5" text-anchor="end">第1</text>
          <text class="axis-label mid" :x="PAD.left - 6" :y="yAt(0.5) + 3.5" text-anchor="end">中位</text>
          <text class="axis-label" :x="PAD.left - 6" :y="yAt(1) + 3.5" text-anchor="end">末位</text>

          <!-- 面积在整条折线底下铺满（跨过退赛的断点）：按段铺会在断点处留下
               一面竖直的"墙"，看着像数据塌了 -->
          <path v-if="areaD" class="area" :fill="areaGradUrl" :d="areaD" />

          <!-- 折线：一段一根（退赛处断开）；含进行中赛事的段画虚线 -->
          <path
            v-for="(seg, i) in chart.segments" :key="`s${i}`"
            class="line" :class="{ dashed: seg.dashed }" :stroke="lineGradUrl"
            :d="linePath(seg.indexes)"
            pathLength="100"
          />

          <!-- 点：已结束=实心环；进行中=虚线环；退赛=灰点（沉在图区下方的灰带里）。
               每个点都垫一个透明大圆做点击热区 —— 4px 的圆点手机上点不准 -->
          <g v-for="(p, i) in chart.points" :key="p.id" @click.stop="select(p)">
            <circle
              v-if="p.id === selectedId" class="halo"
              :cx="xAt(p.x)" :cy="dotY(p)" r="9"
            />
            <circle class="hit" :cx="xAt(p.x)" :cy="dotY(p)" r="10" />
            <!-- 第 1 名的点加一圈金边（和积分榜的 🥇 呼应，一眼看到高光时刻） -->
            <circle
              v-if="!p.unranked && p.rank === 1" class="ring-gold"
              :cx="xAt(p.x)" :cy="yAt(p.y)" r="6.5"
            />
            <circle
              v-if="p.unranked"
              class="dot dropped" :cx="xAt(p.x)" :cy="dotY(p)" r="3"
            />
            <circle
              v-else
              class="dot" :class="{ ongoing: !p.finished, active: p.id === selectedId }"
              :cx="xAt(p.x)" :cy="yAt(p.y)"
              :r="p.id === selectedId ? 5.2 : 3.8"
            />
            <!-- 名次/人数标注：点少时全标；多了只标首尾 + 选中点 -->
            <text
              v-if="showLabel(i, p)" class="dot-label" :class="{ strong: p.id === selectedId }"
              :x="xAt(p.x)" :y="labelY(i)" text-anchor="middle"
            >{{ p.rankLabel }}</text>
          </g>

          <!-- 横轴日期：只标首尾，各带一个小刻度 -->
          <g class="xaxis">
            <line :x1="xAt(0)" :x2="xAt(0)" :y1="yAt(1) + 8" :y2="yAt(1) + 12" />
            <text :x="xAt(0)" :y="H - 3" text-anchor="start">{{ firstLabel }}</text>
            <template v-if="total > 1">
              <line :x1="xAt(1)" :x2="xAt(1)" :y1="yAt(1) + 8" :y2="yAt(1) + 12" />
              <text :x="xAt(1)" :y="H - 3" text-anchor="end">{{ lastLabel }}</text>
            </template>
          </g>
        </svg>
      </div>

      <!-- 图例：只在真的出现那类点时显示，省得每次都念一遍 -->
      <div class="legend">
        <span class="lg"><i class="lg-dot finished" />已结束</span>
        <span v-if="hasOngoing" class="lg"><i class="lg-dot ongoing" />进行中</span>
        <span v-if="hasUnranked" class="lg"><i class="lg-dot dropped" />退赛（不计名次）</span>
      </div>

      <!-- 选中点的详情（点图上的圆点） -->
      <div class="chart-tip" :class="{ empty: !selected }">
        <template v-if="selected">
          <div class="tip-title">{{ selected.title }}</div>
          <div class="tip-line">
            <span v-if="selected.unranked" class="tip-rank dropped">退赛，未计入名次</span>
            <span v-else class="tip-rank">第 {{ selected.rank }} 名 / 共 {{ selected.total }} 人</span>
            <span class="tip-meta">
              {{ selected.matchesWon }} 胜 / {{ selected.matchesPlayed }} 场
              <template v-if="selected.droppedPlayers">
                · 另有 {{ selected.droppedPlayers }} 人退赛
              </template>
              <template v-if="!selected.finished"> · 进行中</template>
              <template v-if="!selected.matchesPlayed"> · 0 场</template>
            </span>
          </div>
        </template>
        <template v-else>
          <span class="tip-idle">点图上的圆点，看那一场的名次</span>
        </template>
      </div>

      <!-- 场次太多时默认只画最近 N 场（再密就点不准、也看不清），这里可展开 -->
      <div v-if="hiddenCount > 0" class="more-row">
        <span class="more-btn" @click="toggleAll">
          {{ showAll ? `只看最近 ${RECENT_LIMIT} 场` : `查看全部 ${allCount} 场` }}
        </span>
        <span class="more-hint">
          {{ showAll ? '点太多会挤在一起' : `还有 ${hiddenCount} 场更早的记录` }}
        </span>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, useId, watch } from 'vue'
import {
  buildRankHistory, pickRecent, smoothPath, summarize, RECENT_LIMIT, Y_TICKS,
  type RankHistoryItem, type RankPoint,
} from '@/utils/rankHistory'

const props = defineProps<{ items: RankHistoryItem[] }>()

// SVG 用固定的逻辑坐标系，靠 viewBox 自适应手机宽度（不用监听 resize）
const W = 320
const H = 168
const PAD = { left: 30, right: 12, top: 20, bottom: 26 }

// 渐变 id 每个实例一份：同一页面若同时活着两个图（比如详情弹层里又开了个人页），
// 固定的 id 会撞车，两条线都去引用先出现的那个 <defs>。用 useId 保证唯一。
const rid = useId()
const lineGradId = `rh-line-${rid}`
const areaGradId = `rh-area-${rid}`
const lineGradUrl = `url(#${lineGradId})`
const areaGradUrl = `url(#${areaGradId})`

/* ---------------- 场次多时折叠到最近 N 场 ----------------
 * 100 个点挤在 320px 里，点间距 3px、圆点直径 9px —— 互相盖住且热区重叠，
 * 挑不中具体某一场；横轴也只剩首尾两个日期。所以超过 RECENT_LIMIT 默认
 * 只画最近的，"查看全部"才铺开。全量顺序仍按时间正序，两端对齐。
 */
const showAll = ref(false)
const selectedId = ref<number | null>(null)
const recent = computed(() => pickRecent(props.items))
const allSorted = computed(() => pickRecent(props.items, 0).items)
const allCount = computed(() => allSorted.value.length)
const hiddenCount = computed(() => recent.value.hidden)
const shownItems = computed(() => (showAll.value ? allSorted.value : recent.value.items))

// 换人看（或重新拉取数据）时回到折叠状态，并清掉可能已不在图上的选中点
watch(() => props.items, () => {
  showAll.value = false
  selectedId.value = null
})

function toggleAll() {
  showAll.value = !showAll.value
  selectedId.value = null   // 选中点可能被折叠掉，别留一个对不上的详情
}

const chart = computed(() => buildRankHistory(shownItems.value))
const sum = computed(() => summarize(chart.value))
const total = computed(() => chart.value.total)
const plotW = computed(() => W - PAD.left - PAD.right)
const hasUnranked = computed(() => chart.value.points.some((p) => p.unranked))
const hasOngoing = computed(() => chart.value.points.some((p) => !p.unranked && !p.finished))
const firstLabel = computed(() => chart.value.points[0]?.dateLabel || '')
const lastLabel = computed(() => chart.value.points[chart.value.points.length - 1]?.dateLabel || '')

/** 平均位置转成"前 X%"（四舍五入到 5%）：0.4 → 前 40%；贴着第 1 的显示"第 1" */
const avgText = computed(() => {
  if (sum.value.avgPos == null) return '-'
  const pct = Math.round((sum.value.avgPos * 100) / 5) * 5
  return pct <= 0 ? '第 1' : `前 ${pct}%`
})

const headSub = computed(() => {
  if (!allCount.value) return ''
  // 折叠时把"总共多少场、现在画的是哪一段"写清楚，别让人以为丢了数据
  if (hiddenCount.value > 0 && !showAll.value) {
    return `共 ${allCount.value} 场 · 显示最近 ${total.value} 场`
  }
  return `${allCount.value} 场 · 越靠上名次越好`
})

const xAt = (x: number) => PAD.left + x * plotW.value
const yAt = (y: number) => PAD.top + y * (H - PAD.top - PAD.bottom)
/** 退赛点所在的灰带中心：画在图区下方，"掉出排名区"一眼可见 */
const bandY = computed(() => yAt(1) + 9)

/** 点的纵向位置（退赛点固定沉在下面的灰带里） */
function dotY(p: RankPoint) {
  return p.unranked ? bandY.value : yAt(p.y)
}
/**
 * 标签默认画在点上方；两种情况要挪：
 * - 贴顶的点 → 改到下方，免得跑出画布；
 * - 折线**下行**进入这个点时（前一点更高）→ 也改到下方：线从左上压过来，
 *   上方没地方了。上行进入时空间在上方，保持不变。
 */
function labelY(i: number) {
  const p = chart.value.points[i]
  const y = dotY(p)
  const prev = chart.value.points[i - 1]
  const comingFromAbove = !!prev && !prev.unranked && !p.unranked && prev.y < p.y
  if (comingFromAbove) return y + 13
  return y - 8 < PAD.top - 4 ? y + 13 : y - 8
}

function px(points: { x: number; y: number }[]) {
  return points.map((q) => ({ x: xAt(q.x), y: yAt(q.y) }))
}
function linePath(indexes: number[]) {
  const pts = indexes.map((i) => chart.value.points[i])
  return smoothPath(px(pts))
}
/** 整条面积：跨过退赛断点铺满，收口到底边（按段铺会在断点留下一面竖墙） */
const areaD = computed(() => {
  const pts = px(chart.value.points.filter((p) => !p.unranked))
  if (pts.length < 2) return ''
  const bottom = yAt(1)
  return `${smoothPath(pts)} L ${pts[pts.length - 1].x} ${bottom} L ${pts[0].x} ${bottom} Z`
})

const selected = computed<RankPoint | null>(
  () => chart.value.points.find((p) => p.id === selectedId.value) || null,
)
function select(p: RankPoint) {
  selectedId.value = selectedId.value === p.id ? null : p.id
}
// 点空白处关掉详情（点本身已经 stop 了）
function onSvgClick() {
  selectedId.value = null
}

/** 点少（≤6）时全标；否则只标首尾 + 选中点，避免标签互相压字 */
function showLabel(index: number, p: RankPoint) {
  if (p.id === selectedId.value) return true
  if (p.unranked) return false        // 退赛点由灰带上的文字统一说明，不再逐个标
  if (total.value > 6) return index === 0 || index === total.value - 1
  return true
}
</script>

<style scoped>
.rank-chart { padding: 2px 0 0; }
.chart-head { display: flex; align-items: baseline; flex-wrap: wrap; gap: 6px 8px; padding: 0 2px 8px; }
.chart-title { font-size: 15px; font-weight: 700; color: #323233; letter-spacing: .2px; }
.chart-sub { font-size: 11px; color: #969799; }

/* ---- 小结三格 ---- */
.metrics {
  display: flex; margin-bottom: 10px;
  background: linear-gradient(135deg, #f3f8ff 0%, #f7fbff 100%);
  border: 1px solid #e8f2fe; border-radius: 12px; overflow: hidden;
}
.metric { flex: 1; display: flex; flex-direction: column; align-items: center; gap: 2px; padding: 9px 0; }
.metric + .metric { border-left: 1px solid #e8f2fe; }
.metric-value { font-size: 16px; font-weight: 700; color: #1989fa; font-variant-numeric: tabular-nums; }
.metric-value.best { color: #e8a800; }
.metric-label { font-size: 10px; color: #969799; }

/* ---- 图卡 ---- */
.chart-card {
  background: #fff; border: 1px solid #f0f2f5; border-radius: 12px;
  padding: 6px 4px 0; box-shadow: 0 1px 3px rgba(16, 55, 96, .04);
}
.chart-svg { width: 100%; height: auto; display: block; touch-action: manipulation; }

.grid { stroke: #eef0f3; stroke-width: 1; }
.grid.base { stroke: #e6e9ee; }
.grid.mid { stroke-dasharray: 3 3; }
.band { fill: #f2f3f5; }
.axis-label { font-size: 9px; fill: #a2a8b1; }
.axis-label.mid { fill: #c1c6cd; }
.xaxis line { stroke: #e6e9ee; stroke-width: 1; }
.xaxis text { font-size: 9px; fill: #b8bdc6; }

/* stroke/fill 的渐变引用走元素属性（:stroke / :fill），因为 id 是每个实例唯一的，
   写不进静态 CSS；CSS 只负责线宽、圆角、动效这些和 id 无关的部分 */
.area { stroke: none; }
.line {
  fill: none; stroke-width: 2.6;
  stroke-linecap: round; stroke-linejoin: round;
  /* 入场动画：pathLength="100" 把长度归一化，不用量真实长度 */
  stroke-dasharray: 100; stroke-dashoffset: 0;
  animation: rh-draw .7s ease-out both;
}
.line.dashed { stroke-dasharray: 4 4; animation: none; }
@keyframes rh-draw { from { stroke-dashoffset: 100 } to { stroke-dashoffset: 0 } }
/* 减弱动效偏好下不做入场动画（虚线段的 dasharray 已在上一条里显式覆盖） */
@media (prefers-reduced-motion: reduce) {
  .line { animation: none }
}

.halo { fill: rgba(25, 137, 250, .14); pointer-events: none; }
.hit { fill: transparent; cursor: pointer; }
.dot {
  fill: #fff; stroke: #1989fa; stroke-width: 2;
  pointer-events: none;
  transition: r .12s ease;
}
.dot.ongoing { stroke: #1989fa; stroke-dasharray: 2.2 2.2; opacity: .75; }
.dot.active { fill: #1989fa; stroke: #fff; stroke-width: 2; }
.dot.dropped { fill: #c2c7cf; stroke: #fff; stroke-width: 1.5; }
.ring-gold { fill: none; stroke: #f5c518; stroke-width: 1.4; pointer-events: none; opacity: .95; }
.dot-label { font-size: 9px; fill: #8a9099; font-variant-numeric: tabular-nums; pointer-events: none; }
.dot-label.strong { fill: #1989fa; font-weight: 700; }

/* ---- 图例 ---- */
.legend { display: flex; flex-wrap: wrap; gap: 12px; padding: 8px 6px 0; }
.lg { display: inline-flex; align-items: center; gap: 4px; font-size: 10px; color: #969799; }
.lg-dot { width: 9px; height: 9px; border-radius: 50%; box-sizing: border-box; }
.lg-dot.finished { background: #fff; border: 2px solid #1989fa; }
.lg-dot.ongoing { background: #fff; border: 2px dotted #1989fa; opacity: .8; }
.lg-dot.dropped { background: #c2c7cf; }

/* ---- 详情 / 提示 ---- */
.chart-tip {
  margin-top: 10px; padding: 9px 12px; background: #f7f8fa;
  border-radius: 10px; min-height: 46px;
}
.chart-tip.empty { display: flex; align-items: center; background: transparent; padding: 4px 2px 0; min-height: 0; }
.tip-title { font-size: 13px; font-weight: 600; color: #323233; margin-bottom: 3px; }
.tip-line { display: flex; align-items: baseline; flex-wrap: wrap; gap: 4px 8px; }
.tip-rank { font-size: 14px; font-weight: 700; color: #1989fa; font-variant-numeric: tabular-nums; }
.tip-rank.dropped { color: #969799; font-size: 13px; }
.tip-meta { font-size: 11px; color: #969799; }
.tip-idle { font-size: 11px; color: #c8c9cc; }

/* ---- 「查看全部 / 只看最近 N 场」 ---- */
.more-row { display: flex; align-items: baseline; gap: 8px; padding: 10px 2px 0; }
.more-btn {
  font-size: 13px; color: #1989fa; cursor: pointer;
  padding: 3px 10px; border-radius: 12px; background: #eaf4fe;
}
.more-btn:active { background: #dbeafe; }
.more-hint { font-size: 11px; color: #c8c9cc; }
</style>
