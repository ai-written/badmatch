"""报名海报需要的二维码接口。

海报本身由前端 canvas 手绘（与积分榜分享图同一套画法），后端只负责把
「报名页地址」画成一张二维码 PNG：

- 前端不必为一张二维码再引入一个依赖；
- 码里的内容由服务端拼装（站点地址 + 赛事 id + 当前用户的邀请码），
  不接受调用方传入的任意文本，免得这个接口变成公开的「任意链接二维码生成器」。
"""
from io import BytesIO

import qrcode
from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.core.security import require_user
from app.models.tournament import Tournament, TournamentStatus
from app.models.user import User

router = APIRouter(prefix="/api/tournaments/{tournament_id}", tags=["poster"])

# 每个模块 12px：海报里二维码按 260 逻辑像素画（2 倍图即 520px），
# 出图比目标尺寸略大一点，缩下去模块边缘才不会糊。
QR_BOX_SIZE = 12
# 静区（四边留白）取规范下限 4 个模块。低于它微信「长按识别」很容易失败；
# 海报里也必须给二维码留够白边，不能把它压在花纹背景上。
QR_BORDER = 4


def poster_path(tournament_id: int, status: str | None = None) -> str:
    """海报二维码指向哪个页面。

    报名中指向报名页；已开赛/已结束指向**对阵表** —— 那时海报上的行动号召就是
    「扫码看赛程」（详见 client 里 posterQrTitle/posterStatusText 的分支），
    扫完还要再点一次「对阵表」就多了一步。
    """
    if status is not None and status != TournamentStatus.OPEN:
        return f"/tournament/{tournament_id}/schedule"
    return f"/tournament/{tournament_id}"


def poster_url(
    tournament_id: int,
    invite_code: str | None = None,
    frontend_url: str | None = None,
    status: str | None = None,
) -> str:
    """海报二维码里要装的地址：目标页 + 邀请码 + 来源标记。

    带邀请码是为了让**新用户**这条路走得通：报名页/对阵表都要求登录，而站点是邀请
    注册制，扫码的人如果被弹到登录页却没有邀请码，就卡死在注册表单前。带上 ?invite= 后，
    未登录访问会被路由守卫转发到 /profile?invite=xxx（见 client/src/router/index.ts），
    注册表单自动预填好邀请码，注册完再回到原目标页。
    ?from=poster 只用于事后在访问日志里区分「海报带来的访问」。
    """
    base = (frontend_url if frontend_url is not None else get_settings().FRONTEND_URL).rstrip("/")
    url = f"{base}{poster_path(tournament_id, status)}?from=poster"
    if invite_code:
        url += f"&invite={invite_code}"
    return url


def qr_png(data: str) -> bytes:
    """把一段文本画成 PNG 二维码（纯函数，便于单测）。"""
    qr = qrcode.QRCode(
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=QR_BOX_SIZE,
        border=QR_BORDER,
    )
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@router.get("/poster-qr")
async def poster_qr(
    tournament_id: int,
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    """报名海报里的二维码（PNG）。

    邀请码一律取**当前登录用户自己的**：海报由本人生成、转发到自己群里，
    印别人的码没有意义；不接受传入的码，也就不会变成「邀请码探测器」。
    """
    # 只取这两列，不要 select(Tournament)：那会把 selectin 的报名/轮次/比赛一起load出来
    row = (
        await db.execute(
            select(Tournament.id, Tournament.status).where(Tournament.id == tournament_id)
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="赛事不存在")

    return Response(
        content=qr_png(poster_url(tournament_id, user.invite_code, status=row.status)),
        media_type="image/png",
    )
