<!--
  全站统一的渐变头（Hero）。

  结构 = 美术图（渐变 + 羽毛球 + 球网）+ 一层极淡的压暗罩 + 插槽内容。
  头本身**不带圆角**：整幅波浪铺满宽度，由下面那张白色浮层（radius 26 + 负 margin）
  压上来形成"卡片盖住图"的层次。之前给头加了下圆角，结果头和浮层之间叠出
  一道台阶（像颗胶囊压着一张卡），和设计稿的过渡不一样。

  几条约束（都是踩过 / 量过的）：
  - 图按**原始比例**铺满顶部，不做 cover 裁切 —— 之前用 cover 把左右裁掉过，
    羽毛球只剩半个。现在头图高度 ≥ 图的自然高度（393 宽时约 141px），
    所以整只球完整可见。
  - 图是 1391×500（≈3.5x@393 宽），手机 3x 屏也是清晰的；
    <picture> 给 WebP + JPEG 兜底 —— 老 iOS（<14）的 WebView 不认 WebP，
    CSS 的 background-image 回落不掉，<img> 才能。
  - 图的下缘有波浪高光，用 mask 最后 8% 淡出到底色，接上任何更矮/更高的头都不露边；
    羽毛球的最低点在图高 91% 处，在淡出区之上，不会被削掉。
  - 顶部压一层很淡的深蓝（10~28%）：白字要压在图最亮的部分（#52a1fe 那一带）时才读得清，
    阴影不够。压暗只覆盖上半部分，下半部分的美术照样鲜亮。
-->
<template>
  <header class="app-hero" :style="{ minHeight: `${height}px` }">
    <picture class="hero-art" aria-hidden="true">
      <source type="image/webp" srcset="/hero/hero.webp" />
      <img src="/hero/hero.jpg" alt="" decoding="async" fetchpriority="high" />
    </picture>
    <span class="hero-scrim" aria-hidden="true" />
    <div class="hero-inner">
      <slot />
    </div>
  </header>
</template>

<script setup lang="ts">
withDefaults(
  defineProps<{
    /** 头部最小高度（px）。图只铺顶部约 141px，高于这个值的部分由底色渐变接续。 */
    height?: number
  }>(),
  { height: 150 },
)
</script>

<style scoped>
.app-hero {
  position: relative;
  overflow: hidden;
  color: #fff;
  flex-shrink: 0;
  /* 兜底色：取自美术图下缘的颜色（左浅右深），图不铺满时接得上 */
  background: linear-gradient(90deg, #7ec3fc 0%, #6bb8ff 55%, #5eb0ff 100%);
}
.hero-art {
  position: absolute;
  top: 0; left: 0; right: 0;
  display: block;
  pointer-events: none;
}
.hero-art img {
  display: block;
  width: 100%;
  height: auto;
  -webkit-mask-image: linear-gradient(to bottom, #000 92%, transparent 100%);
  mask-image: linear-gradient(to bottom, #000 92%, transparent 100%);
}
/* 只在顶部压暗，保证白字对比度；下半部分不压，图保持鲜亮 */
.hero-scrim {
  position: absolute;
  inset: 0;
  pointer-events: none;
  background: linear-gradient(180deg,
    rgba(4, 40, 92, .30) 0%,
    rgba(4, 40, 92, .12) 42%,
    rgba(4, 40, 92, 0) 68%);
}
.hero-inner {
  position: relative;
  padding: 12px 16px 18px;
}
.hero-inner :deep(.hero-title),
.hero-inner :deep(.hero-sub),
.hero-inner :deep(.hero-line) {
  text-shadow: 0 1px 3px rgba(8, 44, 96, .28);
}
</style>
