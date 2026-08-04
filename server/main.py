import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from app.api.auth import router as auth_router
from app.api.tournaments import router as tournament_router
from app.api.matches import router as match_router
from app.api.rankings import router as ranking_router
from app.api.referee import router as referee_router
from app.api.engine_api import router as engine_router
from app.api.notifications import router as notification_router
from app.core.config import get_settings
from app.core.database import engine, Base
from app.core.websocket import manager
from app.models import user, tournament, round

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield


app = FastAPI(title=settings.APP_NAME, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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
    await manager.connect(tournament_id, websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(tournament_id, websocket)
