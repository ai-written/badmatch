#!/usr/bin/env python3
"""
头像存量压缩脚本（一次性 backfill）。

压缩只在上传时执行，历史头像不会自动处理；本脚本手动补齐：
把 uploads 目录里边长大于 100px 的头像压缩到 100px / JPEG 质量 75，
逻辑与 app/api/auth.py 的 _process_avatar 保持一致。

两种模式：

1. 默认（原地覆盖，无需改数据库）：
   docker compose -f docker-compose.prod.yml exec server python scripts/backfill_avatars.py
   同名覆盖，users.avatar 引用不变；非 jpg 文件会变成"jpg 内容 + 旧扩展名"，
   浏览器一般可正常显示，但严格 Content-Type 校验下可能不显示。

2. 严格模式（--rename-jpg，重命名为 .jpg 并同步数据库引用）：
   docker compose -f docker-compose.prod.yml exec server python scripts/backfill_avatars.py --rename-jpg --dry-run
   docker compose -f docker-compose.prod.yml exec server python scripts/backfill_avatars.py --rename-jpg
   自动输出 users 表更新 SQL；容器内可用 --apply-db 自动执行并删除旧文件：
   docker compose -f docker-compose.prod.yml exec server python scripts/backfill_avatars.py --rename-jpg --apply-db

   严格模式会把"所有"非 jpg 头像（无论大小）转换为 JPEG 并改名为 .jpg——
   包括之前原地压缩过、后缀仍是 png/gif/webp 的文件，彻底解决
   后缀与 Content-Type 不一致，以及 CDN/浏览器按旧 URL 缓存的问题
   （改后缀后 URL 变化，缓存立即失效）。

说明：
- 已 <= 100px 的 jpg 自动跳过；严格模式下非 jpg 文件无论大小都会转 jpg
- 重复运行安全
- 严格模式下目标 .jpg 已存在视为已处理（跳过并重复提示 SQL，SQL 幂等可反复执行）
- 数据库引用只存在于 users.avatar（其余展示均为 JOIN 查询）；
  audit_logs.detail 中的历史路径快照不会更新，属正常审计留痕
- 未用 --apply-db 时旧文件保留，SQL 执行成功后可手动删除
"""
import argparse
import os
import tempfile
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
JPEG_EXTS = {".jpg", ".jpeg"}
MAX_SIZE = 100
QUALITY = 75


def compress_avatar(content: bytes) -> bytes:
    """与 app/api/auth.py 的 _process_avatar 保持一致（100px / quality 75）。"""
    img = Image.open(BytesIO(content))
    img = ImageOps.exif_transpose(img)
    img.thumbnail((MAX_SIZE, MAX_SIZE))
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[-1])
        img = bg
    else:
        img = img.convert("RGB")
    buf = BytesIO()
    img.save(buf, "JPEG", quality=QUALITY, optimize=True)
    return buf.getvalue()


def default_upload_dir() -> Path:
    # server/scripts/backfill_avatars.py -> server/static/uploads
    return Path(__file__).resolve().parents[1] / "static" / "uploads"


def _write_file(path: Path, content: bytes) -> None:
    """临时文件 + 原子替换写入。"""
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".avatar-tmp-", suffix=path.suffix or ".jpg")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(content)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
        raise


def _apply_db_updates(upload_dir: Path, renames: list[tuple[str, str]]) -> None:
    """容器内自动执行 users.avatar 引用更新，成功后删除旧文件。"""
    try:
        import asyncio

        from sqlalchemy import text
        from app.core.database import async_session_factory
    except ImportError as exc:
        raise SystemExit(f"[失败] --apply-db 需要应用依赖，请在 server 容器内运行: {exc}")

    async def _run() -> None:
        async with async_session_factory() as session:
            for old, new in renames:
                await session.execute(
                    text("UPDATE users SET avatar = :new WHERE avatar = :old"),
                    {"new": f"/static/uploads/{new}", "old": f"/static/uploads/{old}"},
                )
            await session.commit()

    asyncio.run(_run())
    for old, _new in renames:
        try:
            (upload_dir / old).unlink()
        except OSError as exc:
            print(f"  [警告] 删除旧文件失败: {old} ({exc})")


def main() -> None:
    parser = argparse.ArgumentParser(description="头像存量压缩 backfill（100px / JPEG 75）")
    parser.add_argument("--dir", default=None, help="头像目录，默认 <项目>/server/static/uploads")
    parser.add_argument("--dry-run", action="store_true", help="只预览，不写文件")
    parser.add_argument("--threshold", type=int, default=MAX_SIZE, help="边长超过该值才压缩，默认 100")
    parser.add_argument("--rename-jpg", action="store_true", help="严格模式：非 jpg 文件重命名为 .jpg 并输出数据库更新 SQL")
    parser.add_argument("--apply-db", action="store_true", help="配合 --rename-jpg：容器内自动执行数据库更新并删除旧文件")
    args = parser.parse_args()

    if args.apply_db and not args.rename_jpg:
        parser.error("--apply-db 需要与 --rename-jpg 一起使用")

    upload_dir = Path(args.dir) if args.dir else default_upload_dir()
    if not upload_dir.is_dir():
        parser.error(f"头像目录不存在: {upload_dir}")

    files = sorted(p for p in upload_dir.iterdir() if p.is_file() and not p.name.startswith("."))
    processed = skipped_small = skipped_ext = skipped_handled = unchanged = failed = 0
    saved_bytes = 0
    renames: list[tuple[str, str]] = []
    non_jpg_inplace = 0

    for path in files:
        if path.suffix.lower() not in IMAGE_EXTS:
            skipped_ext += 1
            continue

        try:
            content = path.read_bytes()
            img = Image.open(BytesIO(content))
            width, height = img.size
            img.close()
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            failed += 1
            print(f"  [跳过] 无法读取图片: {path.name} ({exc})")
            continue

        # 严格模式下非 jpg 文件无论大小都要转 JPEG/改名（修复已原地压缩的旧文件）；
        # 大小过滤只用于"压缩瘦身"（jpg 或默认原地模式）
        is_non_jpg = args.rename_jpg and path.suffix.lower() not in JPEG_EXTS
        if not is_non_jpg and max(width, height) <= args.threshold:
            skipped_small += 1
            continue

        try:
            new_content = compress_avatar(content)
        except Exception as exc:
            failed += 1
            print(f"  [失败] 压缩失败: {path.name} ({exc})")
            continue

        if new_content == content:
            unchanged += 1
            continue

        if is_non_jpg:
            # 严格模式：非 jpg -> 新文件 <stem>.jpg，旧文件暂不删除
            new_name = path.stem + ".jpg"
            target = upload_dir / new_name
            if target.exists():
                # 目标已存在 = 之前运行已处理过（uuid 文件名不会天然重名）；
                # 跳过避免重复生成，SQL 仍打印（幂等，覆盖 DB 更新失败后重跑场景）
                renames.append((path.name, new_name))
                skipped_handled += 1
                print(f"  [跳过] 已处理过: {path.name} -> {new_name}")
                continue
            tag = "预览" if args.dry_run else "重命名"
            print(f"  [{tag}] {path.name} -> {new_name}  {len(content)}B -> {len(new_content)}B")
            renames.append((path.name, new_name))
            saved_bytes += len(content) - len(new_content)
            processed += 1
            if not args.dry_run:
                _write_file(target, new_content)
            continue

        tag = "预览" if args.dry_run else "压缩"
        print(f"  [{tag}] {path.name}  {len(content)}B -> {len(new_content)}B")
        saved_bytes += len(content) - len(new_content)
        processed += 1
        if path.suffix.lower() not in JPEG_EXTS:
            non_jpg_inplace += 1
        if not args.dry_run:
            _write_file(path, new_content)

    print()
    print(f"目录: {upload_dir}")
    print(
        f"压缩: {processed} 个 | 跳过(已小): {skipped_small} 个 | "
        f"跳过(非图片): {skipped_ext} 个 | 跳过(已处理): {skipped_handled} 个 | "
        f"未变化: {unchanged} 个 | 失败: {failed} 个"
    )
    print(f"节省流量: {saved_bytes / 1024:.1f} KB" + ("（预览，未实际写入）" if args.dry_run else ""))

    if renames:
        print()
        print("=== 数据库引用更新（严格模式）===")
        for old, new in renames:
            print(f"UPDATE users SET avatar = '/static/uploads/{new}' WHERE avatar = '/static/uploads/{old}';")
        if args.apply_db:
            if args.dry_run:
                print("（--dry-run：未执行数据库更新）")
            else:
                _apply_db_updates(upload_dir, renames)
                print("数据库更新完成，旧文件已删除")
        else:
            print("提示：请先执行以上 SQL；旧文件已保留，SQL 执行成功后可手动删除")
    elif non_jpg_inplace:
        print(f"提示: 有 {non_jpg_inplace} 个非 jpg 文件被原地覆盖为 JPEG 内容（扩展名不变）")


if __name__ == "__main__":
    main()
