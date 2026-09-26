"""WebSocket 一次性连接票据。

为什么要票据：浏览器没法给 WebSocket 握手加自定义头，所以凭证只能放在
URL 里（查询串）。而 nginx 默认会把请求行（含整个 URL）写进 access log，
等于把长期有效的 JWT 明文落盘 —— 任何能读日志、日志聚合或 docker logs
的人都能直接接管会话。

改为：先用普通 HTTP（JWT 走 Authorization 头，不进日志）换一张票据，
再用票据连 WebSocket。票据有三重限制，即使落进日志也无利用价值：
  1. 一次性 —— 验完立即删除，重放无效
  2. 短时 —— 默认 60 秒过期
  3. 不能换 JWT —— 只是连接凭证

存放：进程内存。当前部署是单进程 uvicorn（Dockerfile 的 CMD 未指定
--workers），与项目里 RateLimiter 的做法一致。若将来改多进程，需要换成
Redis 之类的共享存储，否则票据可能落到另一个进程上导致握手失败。
"""
import logging
import secrets
import time

logger = logging.getLogger(__name__)

TICKET_TTL_SECONDS = 60
# 单用户同时最多持有的有效票据数：客户端重连会持续申请，
# 只保留最近的几个，避免长期不清理导致内存无界
MAX_TICKETS_PER_USER = 5
# 全局硬上限：超过后拒绝签发（宁可让客户端稍后重试，也不让内存无界增长）
MAX_TICKETS_TOTAL = 10_000
# 每 N 次操作才做一次全表清理（摊还 O(1)），避免每次签发都 O(N) 扫描
_PRUNE_EVERY = 500

# ticket -> {"user_id": int, "expires_at": float}
_tickets: dict[str, dict] = {}
_op_count = 0


def _prune(now: float) -> None:
    expired = [t for t, v in _tickets.items() if v["expires_at"] <= now]
    for t in expired:
        _tickets.pop(t, None)


def _maybe_prune(now: float) -> None:
    """每 _PRUNE_EVERY 次操作清理一次过期票据，把 O(N) 扫描摊薄。"""
    global _op_count
    _op_count += 1
    if _op_count >= _PRUNE_EVERY:
        _op_count = 0
        _prune(now)


def issue_ticket(user_id: int, ttl: int = TICKET_TTL_SECONDS) -> str:
    """为用户签发一张一次性票据。"""
    now = time.time()
    _maybe_prune(now)

    if len(_tickets) >= MAX_TICKETS_TOTAL:
        # 到上限先做一次全量清理，仍然满就拒绝（不无限增长）
        _prune(now)
        if len(_tickets) >= MAX_TICKETS_TOTAL:
            raise RuntimeError("WebSocket 票据池已满，请稍后重试")

    # 同一用户的旧票据不主动删（可能有一张正在握手中），但限制数量上限
    mine = [t for t, v in _tickets.items() if v["user_id"] == user_id]
    if len(mine) >= MAX_TICKETS_PER_USER:
        for t in mine[: len(mine) - MAX_TICKETS_PER_USER + 1]:
            _tickets.pop(t, None)

    ticket = secrets.token_urlsafe(32)
    _tickets[ticket] = {"user_id": user_id, "expires_at": now + ttl}
    return ticket


def consume_ticket(ticket: str | None) -> int | None:
    """校验并**立即作废**票据，返回 user_id；无效/过期/已用返回 None。

    无论校验结果如何都会删除该票据：失败时删除是为了避免有人拿一个
    过期票据反复试探；成功时删除是为了保证一次性。

    注意：这里不做用户存活/凭证有效性校验（那需要查库）。票据只是
    「签发那一刻的身份断言」，调用方（WebSocket 握手）需要自己再确认
    该用户仍然存在，否则「签发后 60 秒内账号被删」仍能用票据建连。
    """
    if not ticket:
        return None
    now = time.time()
    _maybe_prune(now)
    entry = _tickets.pop(ticket, None)
    if entry is None:
        return None
    if entry["expires_at"] <= now:
        return None
    return entry["user_id"]


def revoke_user(user_id: int) -> None:
    """作废某用户的全部未用票据（登出/改密/删号时调用）。"""
    for t in [t for t, v in _tickets.items() if v["user_id"] == user_id]:
        _tickets.pop(t, None)
