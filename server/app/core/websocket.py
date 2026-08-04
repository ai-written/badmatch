from fastapi import WebSocket
from typing import Any


class ConnectionManager:
    """Manage WebSocket connections grouped by tournament_id."""

    def __init__(self):
        # tournament_id -> list of WebSocket
        self._rooms: dict[int, list[WebSocket]] = {}

    async def connect(self, tournament_id: int, ws: WebSocket):
        await ws.accept()
        self._rooms.setdefault(tournament_id, []).append(ws)

    def disconnect(self, tournament_id: int, ws: WebSocket):
        room = self._rooms.get(tournament_id)
        if room and ws in room:
            try:
                room.remove(ws)
            except ValueError:
                pass

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


manager = ConnectionManager()
