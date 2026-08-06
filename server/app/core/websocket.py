from fastapi import WebSocket
from typing import Any


class ConnectionManager:
    """Manage WebSocket connections grouped by tournament_id.

    同时维护 user_id -> connections 映射，支持按用户踢出
    （登出/修改密码/重置密码后主动断开其所有连接）。
    """

    def __init__(self):
        # tournament_id -> list of WebSocket
        self._rooms: dict[int, list[WebSocket]] = {}
        # user_id -> set of WebSocket（用于凭证失效时主动踢出）
        self._user_sockets: dict[int, set[WebSocket]] = {}

    async def connect(self, tournament_id: int, ws: WebSocket, user_id: int):
        # 握手已由调用方完成（websocket_endpoint 中已 accept），这里只登记
        self._rooms.setdefault(tournament_id, []).append(ws)
        self._user_sockets.setdefault(user_id, set()).add(ws)

    def disconnect(self, tournament_id: int, ws: WebSocket, user_id: int | None = None):
        room = self._rooms.get(tournament_id)
        if room and ws in room:
            try:
                room.remove(ws)
            except ValueError:
                pass
        if user_id is not None:
            sockets = self._user_sockets.get(user_id)
            if sockets:
                sockets.discard(ws)
                if not sockets:
                    self._user_sockets.pop(user_id, None)
        else:
            # 未知 user_id 时按 ws 对象兜底清理
            for uid, sockets in list(self._user_sockets.items()):
                if ws in sockets:
                    sockets.discard(ws)
                    if not sockets:
                        self._user_sockets.pop(uid, None)
                    break

    async def kick_user(self, user_id: int):
        """断开某用户的全部 WebSocket 连接（如登出、改密码、重置密码）。"""
        sockets = self._user_sockets.pop(user_id, None)
        if not sockets:
            return
        for ws in list(sockets):
            try:
                await ws.close(code=4401, reason="凭证已失效")
            except Exception:
                pass
        # 同时从各房间移除
        for tid, room in list(self._rooms.items()):
            for ws in list(room):
                if ws in sockets:
                    try:
                        room.remove(ws)
                    except ValueError:
                        pass
            if not room:
                self._rooms.pop(tid, None)

    async def broadcast(self, tournament_id: int, message: dict[str, Any]):
        room = self._rooms.get(tournament_id, [])
        stale = []
        for ws in list(room):
            try:
                await ws.send_json(message)
            except Exception:
                stale.append(ws)
        for ws in stale:
            try:
                room.remove(ws)
            except ValueError:
                pass
            for uid, sockets in list(self._user_sockets.items()):
                if ws in sockets:
                    sockets.discard(ws)
                    if not sockets:
                        self._user_sockets.pop(uid, None)
                    break


manager = ConnectionManager()
