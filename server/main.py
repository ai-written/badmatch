import os, logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select
from app.api.auth import router as auth_router
from app.api.tournaments import router as tournament_router
from app.api.matches import router as match_router
from app.api.rankings import router as ranking_router
from app.api.referee import router as referee_router
from app.api.engine_api import router as engine_router
from app.api.notifications import router as notification_router
from app.core.config import get_settings
from app.core.database import engine, Base, async_session_factory
from app.core.security import decode_access_token
from app.core.startup_migration import run_startup_migrations
from app.core.websocket import manager
from app.core.access_log import AccessLogMiddleware
from app.core.etag import ETagMiddleware
from app.core.audit import cleanup_expired_audit_logs
from app.models import user, tournament, round, audit
from app.models.user import User

logger = logging.getLogger(__name__)
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 生产环境必须使用强随机 SECRET_KEY，否则拒绝启动（防 JWT 伪造）
    if not settings.DEBUG and settings.secret_key_is_default():
        raise RuntimeError(
            "生产环境禁止使用默认 SECRET_KEY！请在环境变量中设置强随机密钥 "
            "（如 openssl rand -hex 32），再重新启动。"
        )
    async with engine.begin() as conn:
        await run_startup_migrations(conn)
        await conn.run_sync(Base.metadata.create_all)
        # 启动时清理过期审计记录
        await cleanup_expired_audit_logs(conn)
    yield


app = FastAPI(title=settings.APP_NAME, lifespan=lifespan)

# API 响应 ETag 中间件（最内层：GET 响应生成 ETag，命中 If-None-Match 时返回 304）
app.add_middleware(ETagMiddleware)
# 访问日志中间件（记录所有 HTTP 请求）
app.add_middleware(AccessLogMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(tournament_router)
app.include_router(match_router)
app.include_router(ranking_router)
app.include_router(referee_router)
app.include_router(engine_router)
app.include_router(notification_router)

# Static files for avatars
static_dir = os.path.join(os.path.dirname(__file__), "static")
os.makedirs(os.path.join(static_dir, "uploads"), exist_ok=True)
app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.websocket("/ws/tournaments/{tournament_id}")
async def websocket_endpoint(websocket: WebSocket, tournament_id: int):
    token = websocket.query_params.get("token")
    payload = decode_access_token(token) if token else None
    await websocket.accept()
    user = None
    if payload and payload.get("sub") is not None:
        try:
            uid = int(payload["sub"])
        except (TypeError, ValueError):
            uid = None
        if uid is not None:
            # 手动短会话：校验完立即释放数据库连接，
            # 避免每个长连接占用连接池导致池耗尽
            async with async_session_factory() as session:
                result = await session.execute(select(User).where(User.id == uid))
                u = result.scalar_one_or_none()
                # 同时校验 token 版本号（旧 token 按 0 处理），登出/作废后的 token 不可订阅
                if u is not None and payload.get("tv", 0) == u.token_version:
                    user = u
    if user is None:
        await websocket.close(code=4401, reason="未授权")
        return
    await manager.connect(tournament_id, websocket, user.id)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(tournament_id, websocket)
