# -*- coding: utf-8 -*-
"""交付前自检：确认图里的二维码真能扫出目标地址。

用两个互相独立的解码器交叉验证（zxing-cpp 与 OpenCV），每种图都过三关：
  1. 原始尺寸；
  2. 缩到手机屏宽 400px（群里点开预览的尺寸）；
  3. 存成质量 75 的 JPEG（微信转发压缩后的常态）。

三关全过、两个解码器都认，才算这张图可以发出去。任一关失败直接非零退出。
"""
from __future__ import annotations

import io
import os

import numpy as np
from PIL import Image

try:
    import zxingcpp
except ImportError:  # pragma: no cover
    zxingcpp = None
try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None

from make_invite_qr import URL, HERE

FILES = ["爱玩羽社-邀请二维码.png", "爱玩羽社-分享链接卡片.png"]


def as_np(img: Image.Image) -> np.ndarray:
    return np.array(img.convert("RGB"))


def decode_zxing(img: Image.Image) -> list[str]:
    if zxingcpp is None:
        return []
    return [r.text for r in zxingcpp.read_barcodes(as_np(img))]


def decode_cv2(img: Image.Image) -> list[str]:
    if cv2 is None:
        return []
    gray = cv2.cvtColor(as_np(img), cv2.COLOR_RGB2GRAY)
    ok, texts, _, _ = cv2.QRCodeDetector().detectAndDecodeMulti(gray)
    if not ok:
        one, _, _ = cv2.QRCodeDetector().detectAndDecode(gray)
        return [one] if one else []
    return [t for t in texts if t]


def as_jpeg(img: Image.Image) -> Image.Image:
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=75)
    buf.seek(0)
    return Image.open(buf)


def check(path: str) -> bool:
    img = Image.open(path)
    variants = [
        ("原始尺寸", img),
        ("缩到手机屏宽 400px", img.resize((400, round(img.height * 400 / img.width)), Image.LANCZOS)),
        ("JPEG 质量 75", as_jpeg(img)),
    ]
    print(f"\n== {os.path.basename(path)}  ({img.width}x{img.height}) ==")
    ok_all = True
    for label, variant in variants:
        z = decode_zxing(variant)
        c = decode_cv2(variant)
        ok = URL in z and URL in c
        ok_all &= ok
        print(
            f"  [{'OK ' if ok else 'FAIL'}] {label:<20} "
            f"zxing={'命中' if URL in z else '未命中'}  opencv={'命中' if URL in c else '未命中'}"
            f"  解出={z or c or '<无>'}"
        )
    return ok_all


if __name__ == "__main__":
    # Windows 控制台默认 GBK，中文+符号混排容易炸编码；统一按 UTF-8 输出
    try:
        import sys

        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # pragma: no cover
        pass
    print("期望内容:", URL)
    all_ok = all(check(os.path.join(HERE, f)) for f in FILES)
    print("\n结果:", "全部通过" if all_ok else "存在失败项")
    raise SystemExit(0 if all_ok else 1)
