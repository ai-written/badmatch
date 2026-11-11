# design/ —— 设计稿与素材源文件

这里放的是**设计阶段的源文件**，不参与构建、不会打进前端产物。
线上真正用的素材是脚本从这里生成、再放进 `client/public/` 的那几份。

```
design/
├── source/
│   ├── badminton_source.jpg      # 设计稿原图（1391×3013），所有素材都从它裁
│   ├── badminton_no_list.jpg     # 同一张把列表区域抹平后的版本（做过背景实验，留档）
│   ├── shuttle_purple.jpg        # 首版单张贴片（263×265），已被下面的产物取代
│   ├── shuttle_orange.jpg
│   ├── shuttle_mint.jpg
│   ├── shuttle_hero.jpg          # 头图里的羽毛球（未使用，头图用的是整条背景）
│   └── shuttlecock_clean.png     # 早期用 PIL 手绘的矢量风格羽毛球（对比用）
├── build_tiles.py                # ★ 生成 client/public/tile/*.jpg（当前线上用的）
├── make_badminton_assets.py      # 最初的素材拆分脚本（历史）
└── draw_clean_shuttle.py         # 手绘羽毛球（历史）
```

## 素材 → 产物对照

| 产物（线上用） | 生成方式 |
|---|---|
| `client/public/hero/hero.webp`（1391×500，27KB）<br>`client/public/hero/hero.jpg`（786×283，21KB，老浏览器兜底） | 从 `source/badminton_source.jpg` 顶部 0..500px 直接裁；<br>**图里没有任何文字**，站名/加号都是 DOM |
| `client/public/tile/tile-{purple,orange,mint}.jpg`（各 4KB） | `python design/build_tiles.py`（见脚本头部说明） |

## 三条踩过的坑（改素材前务必先看）

1. **头图不能 `cover`**：按 `cover` 缩放会把左右裁掉，羽毛球只剩半个。
   现在是 `width:100%; height:auto` 按原比例铺顶部。
2. **贴片不能用"图片 + CSS 纯色底"两层拼**：两者颜色差一点就露出一条竖直接缝。
   现在在图片里就把底色（插画自身模糊）和插画拼成一张。
3. **CSS 背景图不能用 WebP 当唯一格式**：解码失败是**静默**的，老 iOS/老微信
   WebView 上只会显示 `background-color`，表现成"插画消失、只剩色块"（线上真出现过）。
   所以贴片导出 JPEG；头图用 `<picture>` 给 WebP + JPEG 两个源。
