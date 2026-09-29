import time
from threading import Lock


class RateLimiter:
    """简单内存**固定窗口**限流：同一 key 在窗口内最多允许 max_attempts 次失败。

    注意是固定窗口不是滑动窗口：窗口起点锚在该 key 第一次 check 的时刻，
    过期整体清零。因此跨窗口边界时，短时间内最多可能放行 2×max_attempts
    （窗口末尾用满一批 + 新窗口再放行一批）——设置阈值时按这个最坏情况留余量。
    """

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

    def reset_all(self) -> None:
        """清空所有 key 的计数。

        给「管理员主动解锁」这类场景用（见 admin_reset_password）：按用户名那把可以精确
        reset，但按 IP 的兜底是共享的，谁被它拦住、从哪个 IP 失败过都不好一一对应，
        所以整体归零最可靠。
        """
        with self._lock:
            self._data.clear()
