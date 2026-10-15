# -*- coding: utf-8 -*-
"""生成「爱玩羽社」邀请二维码分享图。

产出两张图（都放在本目录下）：
  1. 爱玩羽社-邀请二维码.png   竖版分享海报卡片（主图，适合发微信群）
  2. 爱玩羽社-分享链接卡片.png  横版链接卡片（类似微信里分享链接的样式）

设计语言跟项目里已有的两张分享图（client/src/utils/poster.ts 报名海报、
src/utils/shareCard.ts 积分榜）保持一致：蓝色渐变头部 #1989fa -> #0b6fd0、
白色卡片、#f7f8fa 圆角二维码卡片、#969799 辅助文字。

二维码用 segno 生成（ECC 级别 H），再按模块手绘：
圆角模块 + 品牌 logo 居中挖孔（H 级 30% 冗余，7x7 模块的挖孔不影响力）。
"""
from __future__ import annotations

import math
import os

import segno
from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

URL = "https://aiwanyushe.hsianglee.cn/"
LOGO_PATH = os.path.join(ROOT, "client", "src", "assets", "icon.png")

FONT_REG = r"C:\Windows\Fonts\msyh.ttc"
FONT_BOLD = r"C:\Windows\Fonts\msyhbd.ttc"

# 取自 client/src/utils/poster.ts 的配色
BLUE_1 = (25, 137, 250)
BLUE_2 = (11, 111, 208)
INK = (51, 51, 51)
GREY = (150, 151, 153)
LINE = (242, 243, 245)
CARD_BG = (247, 248, 250)

_font_cache: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    key = ("b" if bold else "r", size)
    if key not in _font_cache:
        _font_cache[key] = ImageFont.truetype(FONT_BOLD if bold else FONT_REG, size)
    return _font_cache[key]


def text_w(draw: ImageDraw.ImageDraw, s: str, f: ImageFont.FreeTypeFont) -> float:
    return draw.textbbox((0, 0), s, font=f)[2]


def draw_center(draw, cx, y, s, f, fill):
    """以 cx 为水平中心、y 为文字顶部画一行字。"""
    draw.text((cx - text_w(draw, s, f) / 2, y), s, font=f, fill=fill)


def vgradient(size, top, bottom) -> Image.Image:
    w, h = size
    img = Image.new("RGB", (1, h))
    px = img.load()
    for y in range(h):
        t = y / max(1, h - 1)
        px[0, y] = tuple(round(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
    return img.resize((w, h), Image.BILINEAR)


def rgba(size) -> Image.Image:
    return Image.new("RGBA", size, (0, 0, 0, 0))


def rounded_shadow(base: Image.Image, box, radius, blur=26, offset=(0, 14), alpha=70):
    """在 base 上叠一层圆角投影。"""
    x0, y0, x1, y1 = box
    layer = rgba(base.size)
    ImageDraw.Draw(layer).rounded_rectangle(
        (x0, y0 + offset[1], x1 + offset[0], y1 + offset[1]), radius, fill=(15, 35, 60, alpha)
    )
    base.alpha_composite(layer.filter(ImageFilter.GaussianBlur(blur)))


def qr_matrix(data: str) -> list[list[int]]:
    qr = segno.make(data, error="h")
    return [[int(v) for v in row] for row in qr.matrix]


def draw_qr(
    canvas: Image.Image,
    data: str,
    box,
    *,
    dark=(16, 42, 67),
    logo: Image.Image | None = None,
    logo_modules: int = 7,
    module_radius: float = 0.34,
):
    """在 box=(x0,y0,size) 的方形区域里画二维码（含 4 模块静区）。

    返回实际绘制用的模块尺寸，便于外部对齐。静区由外层白底提供，这里保证
    4 个模块的留白一定在 box 内，不依赖调用方。
    """
    m = qr_matrix(data)
    n = len(m)
    x0, y0, side = box
    quiet = 4
    module = side / (n + quiet * 2)
    radius = max(1, round(module * module_radius))

    draw = ImageDraw.Draw(canvas)
    origin_x = x0 + quiet * module
    origin_y = y0 + quiet * module

    def cell(r: int, c: int):
        return (
            origin_x + c * module,
            origin_y + r * module,
            origin_x + (c + 1) * module,
            origin_y + (r + 1) * module,
        )

    finders = {(0, 0), (0, n - 7), (n - 7, 0)}
    in_finder = lambda r, c: any(  # noqa: E731
        fr <= r < fr + 7 and fc <= c < fc + 7 for fr, fc in finders
    )

    # 中心 logo 挖孔范围（模块坐标，奇数格保证左右对称）
    half = logo_modules // 2
    center = n // 2
    hole = (center - half, center - half, center + half, center + half)

    for r in range(n):
        for c in range(n):
            if not m[r][c] or in_finder(r, c):
                continue
            if hole[0] <= r <= hole[2] and hole[1] <= c <= hole[3]:
                continue
            draw.rounded_rectangle(cell(r, c), radius=radius, fill=dark)

    # 定位角：外框必须保持方正的 1:1:3:1:1 结构，只把中心画成圆角
    # （早期版本把外框也做了圆角，人眼看着更柔，但二维码直接解不出来了——
    #  OpenCV 的定位算法在圆角上扫不到那条标准扫描线，别改回去）
    for fr, fc in finders:
        draw.rectangle(
            (origin_x + fc * module, origin_y + fr * module,
             origin_x + (fc + 7) * module, origin_y + (fr + 7) * module), fill=dark,
        )
        draw.rectangle(
            (origin_x + (fc + 1) * module, origin_y + (fr + 1) * module,
             origin_x + (fc + 6) * module, origin_y + (fr + 6) * module), fill=(255, 255, 255),
        )
        draw.rounded_rectangle(
            (origin_x + (fc + 2) * module, origin_y + (fr + 2) * module,
             origin_x + (fc + 5) * module, origin_y + (fr + 5) * module),
            radius=round(module * 1.0), fill=dark,
        )

    if logo is not None:
        hole_px = logo_modules * module
        hx = origin_x + hole[1] * module
        hy = origin_y + hole[0] * module
        # 挖孔用圆形：比如方形挖孔更贴近"圆点码"的观感，也不容易看出刀口
        d = ImageDraw.Draw(canvas)
        d.ellipse((hx, hy, hx + hole_px, hy + hole_px), fill=(255, 255, 255))
        size = round(hole_px * 0.82)
        lg = load_logo(size)
        mask = Image.new("L", (size, size), 0)
        ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
        canvas.paste(lg, (round(hx + (hole_px - size) / 2), round(hy + (hole_px - size) / 2)), mask)

    return module


def load_logo(size: int) -> Image.Image:
    """品牌 logo：裁成正方形 + 圆形蒙版，边缘带一圈白，便于压在深色/二维码上。"""
    src = Image.open(LOGO_PATH).convert("RGBA")
    side = min(src.size)
    src = src.crop(
        ((src.width - side) // 2, (src.height - side) // 2,
         (src.width + side) // 2, (src.height + side) // 2)
    ).resize((size, size), Image.LANCZOS)
    return src


def logo_in_ring(canvas: Image.Image, cx: int, cy: int, outer: int, logo_ratio=0.8,
                 ring=6, ring_color=(255, 255, 255), bg=(255, 255, 255)):
    d = ImageDraw.Draw(canvas)
    d.ellipse((cx - outer / 2, cy - outer / 2, cx + outer / 2, cy + outer / 2), fill=bg)
    size = round(outer * logo_ratio)
    lg = load_logo(size)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
    canvas.paste(lg, (round(cx - size / 2), round(cy - size / 2)), mask)
    if ring:
        d.ellipse(
            (cx - outer / 2, cy - outer / 2, cx + outer / 2, cy + outer / 2),
            outline=ring_color, width=ring,
        )


def decor(canvas: Image.Image, w: int, h: int, tint=(255, 255, 255)):
    """背景装饰：柔光 + 大圆弧 + 小球场线条，克制一点，别抢二维码。"""
    layer = rgba((w, h))
    d = ImageDraw.Draw(layer)
    d.ellipse((-w * 0.35, -h * 0.22, w * 0.9, h * 0.30), fill=tint + (26,))
    d.ellipse((w * 0.55, -h * 0.14, w * 1.5, h * 0.26), outline=tint + (46,), width=3)
    d.ellipse((w * 0.72, -h * 0.06, w * 1.35, h * 0.18), outline=tint + (34,), width=2)
    d.arc((-w * 0.25, h * 0.80, w * 0.60, h * 1.30), 200, 340, fill=tint + (40,), width=3)
    d.arc((w * 0.70, h * 0.86, w * 1.40, h * 1.34), 200, 340, fill=tint + (30,), width=2)
    canvas.alpha_composite(layer)


# ---------------------------------------------------------------- 竖版主图

def build_poster(out_name="爱玩羽社-邀请二维码.png"):
    W, H = 1080, 1636
    canvas = vgradient((W, H), BLUE_1, (9, 92, 176)).convert("RGBA")
    decor(canvas, W, H)

    d = ImageDraw.Draw(canvas)
    cx = W // 2

    # 顶部品牌区
    pill_w, pill_h = 244, 58
    d.rounded_rectangle(
        (cx - pill_w / 2, 66, cx + pill_w / 2, 66 + pill_h), pill_h / 2,
        fill=(255, 255, 255, 46), outline=(255, 255, 255, 110), width=2,
    )
    f_pill = font(27)
    draw_center(d, cx, 66 + (pill_h - 38) / 2, "专 属 邀 请", f_pill, (255, 255, 255))

    logo_in_ring(canvas, cx, 232, 168, ring=6, ring_color=(255, 255, 255, 235))
    d = ImageDraw.Draw(canvas)
    draw_center(d, cx, 330, "爱玩羽社", font(46, True), (255, 255, 255))
    draw_center(d, cx, 396, "羽毛球循环赛 · 双打随机搭档", font(27), (222, 238, 255))

    # 白色主卡片
    card = (72, 470, W - 72, 1490)
    rounded_shadow(canvas, card, 42, blur=30, offset=(0, 16), alpha=76)
    d = ImageDraw.Draw(canvas)
    d.rounded_rectangle(card, 42, fill=(255, 255, 255))

    cxx = cx
    top = card[1]
    draw_center(d, cxx, top + 62, "邀请你加入爱玩羽社", font(50, True), INK)
    draw_center(d, cxx, top + 136, "约球 · 记分 · 看积分榜，一个链接就够了", font(28), GREY)

    # 二维码白板
    tile = 560
    tile_x0 = cxx - tile / 2
    tile_y0 = top + 188
    d.rounded_rectangle(
        (tile_x0, tile_y0, tile_x0 + tile, tile_y0 + tile), 36,
        fill=(255, 255, 255), outline=(233, 238, 244), width=3,
    )
    qr_side = 496
    draw_qr(
        canvas, URL,
        (tile_x0 + (tile - qr_side) / 2, tile_y0 + (tile - qr_side) / 2, qr_side),
        logo=load_logo(140),
    )

    # 二维码下方文案
    y = tile_y0 + tile + 34
    draw_center(d, cxx, y, "微信扫一扫 / 长按识别二维码", font(30, True), (60, 66, 72))
    draw_center(d, cxx, y + 50, "手机扫码即可进入，微信内打开体验最佳", font(23), (160, 168, 176))

    # 链接药丸
    f_link = font(26, True)
    lw = text_w(d, URL, f_link)
    pw, ph = lw + 76, 68
    py = y + 100
    d.rounded_rectangle((cxx - pw / 2, py, cxx + pw / 2, py + ph), ph / 2, fill=(234, 244, 255))
    d.text((cxx - lw / 2, py + (ph - 34) / 2 - 1), URL, font=f_link, fill=(11, 111, 208))

    # 底部页脚
    fy = 1530
    draw_center(d, cx, fy, "长按保存图片 · 转发给群里的球友", font(26), (216, 234, 252))
    draw_center(d, cx, fy + 54, "爱玩羽社 · 一起打球", font(24), (170, 205, 242))

    out = os.path.join(HERE, out_name)
    canvas.convert("RGB").save(out, "PNG", optimize=True)
    print("saved", out, canvas.size)
    return out


# ------------------------------------------------------- 横版「分享链接」卡片

def build_link_card(out_name="爱玩羽社-分享链接卡片.png"):
    W, H = 1280, 700
    canvas = vgradient((W, H), (233, 240, 250), (206, 222, 242)).convert("RGBA")
    decor(canvas, W, H, tint=(255, 255, 255))

    d = ImageDraw.Draw(canvas)
    cx = W // 2
    draw_center(d, cx, 60, "扫 码 或 点 击 链 接 进 入", font(28, True), (108, 128, 152))

    card = (60, 150, W - 60, 570)
    rounded_shadow(canvas, card, 34, blur=30, offset=(0, 14), alpha=64)
    d = ImageDraw.Draw(canvas)
    d.rounded_rectangle(card, 34, fill=(255, 255, 255))

    # 左列：logo + 标题 + 一句话介绍 + 链接药丸，整体按卡片中线居中
    x0 = card[0] + 64
    cy = (card[1] + card[3]) / 2
    logo_in_ring(canvas, x0 + 66, cy, 136, ring=4, ring_color=(238, 242, 247))
    d = ImageDraw.Draw(canvas)

    tx = x0 + 158
    d.text((tx, cy - 96), "爱玩羽社 · 羽毛球循环赛", font=font(40, True), fill=(28, 38, 51))
    d.text((tx, cy - 22), "随机搭档 · 实时记分 · 积分排名", font=font(27), fill=(126, 140, 156))

    f_link = font(24, True)
    lw = text_w(d, URL, f_link)
    pw, ph = lw + 64, 62
    py = cy + 34
    d.rounded_rectangle((tx, py, tx + pw, py + ph), ph / 2, fill=(232, 243, 255))
    d.text((tx + 32, py + (ph - 32) / 2 - 1), URL, font=f_link, fill=(25, 118, 210))

    # 右侧二维码（留得够大：图被转发压缩后仍要能扫出来）
    box = 300
    bx = card[2] - 56 - box
    by = cy - box / 2
    d.rounded_rectangle((bx - 16, by - 16, bx + box + 16, by + box + 16), 28,
                        fill=(255, 255, 255), outline=(226, 234, 242), width=3)
    draw_qr(canvas, URL, (bx, by, box), logo=load_logo(72), logo_modules=5)

    d = ImageDraw.Draw(canvas)
    draw_center(d, cx, 618, "长按识别二维码，或把这张图直接转发给球友", font(26), (96, 116, 140))

    out = os.path.join(HERE, out_name)
    canvas.convert("RGB").save(out, "PNG", optimize=True)
    print("saved", out, canvas.size)
    return out


if __name__ == "__main__":
    a = build_poster()
    b = build_link_card()
    print("URL:", URL, "| files:", os.path.basename(a), os.path.basename(b))
