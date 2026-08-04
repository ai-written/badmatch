import time
from threading import Lock


class RateLimiter:
    """简单内存滑动窗口限流：同一 key 在窗口内最多允许 max_attempts 次失败。"""

    def __init__(self, max_attempts: int, window_seconds: int):
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self._data: dict[str, dict] = {}
        self._lock = Lock()
        self._op_count = 0

    def _prune(self) -> None:
        """清理过期 key，防止内存无限增长。"""
        now = time.time()
        expired = [
            key for key, entry in self._data.items()
            if now - entry["start"] >= self.window_seconds
        ]
        for key in expired:
            del self._data[key]

    def check(self, key: str) -> bool:
        with self._lock:
            now = time.time()
            self._op_count += 1
            # 每 500 次操作或数据量过大时清理一次，均摊 O(1)
            if self._op_count >= 500 or len(self._data) > 10000:
                self._op_count = 0
                self._prune()
            entry = self._data.get(key)
            if entry and now - entry["start"] >= self.window_seconds:
                del self._data[key]
                entry = None
            if not entry:
                self._data[key] = {"start": now, "count": 0}
                return True
            return entry["count"] < self.max_attempts

    def record_failure(self, key: str) -> None:
        with self._lock:
            now = time.time()
            entry = self._data.get(key)
            if not entry or now - entry["start"] >= self.window_seconds:
                self._data[key] = {"start": now, "count": 1}
            else:
                entry["count"] += 1

    def reset(self, key: str) -> None:
        with self._lock:
            self._data.pop(key, None)
