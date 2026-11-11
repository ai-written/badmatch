"""
生成首页日期块用的三张贴片（client/public/tile/tile-{purple,orange,mint}.jpg）。

为什么要单独一个脚本：这三张不是"从设计稿直接裁下来"那么简单，中间有三步处理，
任何一步漏掉都会出问题（都踩过）：

1. **往里缩 20px 再裁**：设计稿里的贴片自带圆角，直接拿来当背景会和 CSS 的
   border-radius 叠成"双圆角"，边缘出现一圈浅色描边。
2. **构图只取紫色那张**：三张原图里羽毛球在各自裁切框中的位置/角度并不一致
   （紫偏上、橙偏左下、薄荷居中），三张各用各的会出现"某一张靠下"。
   所以只用紫色的构图，另外两张用**色相旋转**生成，保证位置完全一致。
3. **底色与插画在图片里就拼好**：底色 = 插画自身铺满后重度模糊（颜色/色调都来自
   插画本身，不会有色差接缝）；插画缩到 72% 贴到右下角，左缘 28% 羽化。
   （不要用 "图 + CSS 纯色底" 两层拼——那样会露出一条竖直接缝。）
4. **导出 JPEG 而不是 WebP**：CSS 的 background-image 解码失败是**静默**的，
   老 iOS / 老微信 WebView 不支持 WebP 时只会显示底色（线上真出现过）。

用法：
    python design/build_tiles.py
"""
import os

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
SRC = os.path.join(ROOT, 'design', 'source', 'badminton_source.jpg')
OUT = os.path.join(ROOT, 'client', 'public', 'tile')

# 设计稿大图（1391×3013）里三个贴片的位置
BOXES = {
    'tile-purple': (53, 1832, 316, 2097),
    'tile-orange': (53, 2140, 316, 2405),
    'tile-mint': (53, 2435, 316, 2703),
}
INSET = 20      # 去掉贴片自带的圆角边缘
SCALE = 0.72    # 羽毛球缩到 72%
RIGHT = 12      # 右下角偏移
DOWN = 2
FEATHER = 0.28  # 插画左缘羽化比例
BLUR = 26       # 底色模糊半径


def median_hue(art: Image.Image) -> int:
    """贴片主色相（只统计高饱和像素），用来算三张之间的色相偏移。"""
    hsv = np.asarray(art.convert('HSV'), dtype=np.int16)
    sat = hsv[..., 1]
    return int(np.median(hsv[..., 0][sat > 40]))


def compose(art: Image.Image) -> Image.Image:
    """把贴片做成一整张：模糊底 + 缩小靠右下的插画（左缘羽化）。"""
    W, H = art.size
    aw, ah = int(W * SCALE), int(H * SCALE)

    canvas = art.resize((W, H), Image.LANCZOS).filter(ImageFilter.GaussianBlur(BLUR))
    small = art.resize((aw, ah), Image.LANCZOS)

    mask = Image.new('L', (aw, ah), 0)
    md = ImageDraw.Draw(mask)
    ramp = int(aw * FEATHER)
    for x in range(aw):
        md.line((x, 0, x, ah), fill=255 if x >= ramp else int(255 * x / ramp))

    canvas.paste(small, (W - aw + RIGHT, H - ah + DOWN), mask)
    return canvas


def main() -> None:
    os.makedirs(OUT, exist_ok=True)
    src = Image.open(SRC).convert('RGB')
    arts = {
        name: src.crop((l + INSET, t + INSET, r - INSET, b - INSET))
        for name, (l, t, r, b) in BOXES.items()
    }
    base = compose(arts['tile-purple'])
    base.save(os.path.join(OUT, 'tile-purple.jpg'), 'JPEG', quality=92, optimize=True)

    # 另外两张：整张色相旋转（浅色底一起转），构图与紫色完全一致
    purple_hue = median_hue(arts['tile-purple'])
    for name in ('tile-orange', 'tile-mint'):
        delta = median_hue(arts[name]) - purple_hue
        hsv = np.asarray(base.convert('HSV'), dtype=np.int16)
        hsv[..., 0] = (hsv[..., 0] + delta) % 256
        out = Image.fromarray(hsv.astype(np.uint8), 'HSV').convert('RGB')
        out.save(os.path.join(OUT, f'{name}.jpg'), 'JPEG', quality=92, optimize=True)
        print(f'{name}.jpg  色相偏移 {delta:+d}')

    print('done →', OUT)


if __name__ == '__main__':
    main()
