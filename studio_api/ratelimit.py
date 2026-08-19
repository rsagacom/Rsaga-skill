"""可选 Redis 共享限流，Redis 不可用时回退到进程内窗口。

限流不是业务账本：Redis 故障默认不阻断创作请求，而是降级为本地保护；
生产若更重视拒绝风险，可设置 ``STUDIO_RATE_LIMIT_FAIL_CLOSED=true``。
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from typing import Any

try:  # Redis 是生产可选依赖，SQLite 本地开发不应因未安装而失效。
    from redis.asyncio import Redis
except ImportError:  # pragma: no cover - exercised in minimal local environments
    Redis = None  # type: ignore[assignment,misc]


logger = logging.getLogger("studio_api.rate_limit")


class RateLimiter:
    """固定窗口限流器，支持 Redis Lua 原子计数和本地降级。"""

    _INCREMENT_SCRIPT = """
local current = redis.call('INCR', KEYS[1])
if current == 1 then
  redis.call('EXPIRE', KEYS[1], ARGV[1])
end
return current
"""

    def __init__(
        self,
        limit: int = 0,
        window_seconds: int = 60,
        redis_url: str | None = None,
        fail_closed: bool = False,
    ) -> None:
        self.limit = max(0, int(limit))
        self.window_seconds = max(1, int(window_seconds))
        self.redis_url = redis_url.strip() if redis_url else ""
        self.fail_closed = bool(fail_closed)
        self._redis: Any | None = None
        self._redis_warning_emitted = False
        self._local_events: dict[str, deque[float]] = defaultdict(deque)

    @property
    def backend(self) -> str:
        if not self.limit:
            return "disabled"
        if self.redis_url and Redis is not None:
            return "redis-with-local-fallback"
        return "local"

    async def allow(self, key: str) -> bool:
        if not self.limit:
            return True
        if self.redis_url:
            redis_decision = await self._allow_redis(key)
            if redis_decision is not None:
                return redis_decision
            if self.fail_closed:
                return False
        return self._allow_local(key)

    async def _allow_redis(self, key: str) -> bool | None:
        if Redis is None:
            if not self._redis_warning_emitted:
                logger.warning("redis rate limiting requested but redis package is unavailable; using local fallback")
                self._redis_warning_emitted = True
            return None
        try:
            if self._redis is None:
                self._redis = Redis.from_url(
                    self.redis_url,
                    decode_responses=False,
                    socket_connect_timeout=0.5,
                    socket_timeout=0.5,
                    health_check_interval=30,
                )
            redis_key = f"studio:rate-limit:{key}"
            current = await self._redis.eval(self._INCREMENT_SCRIPT, 1, redis_key, self.window_seconds)
            return int(current) <= self.limit
        except Exception as exc:  # Redis outage must be observable and bounded.
            if not self._redis_warning_emitted:
                logger.warning("redis rate limiting unavailable; using local fallback error_type=%s", type(exc).__name__)
                self._redis_warning_emitted = True
            if self._redis is not None:
                try:
                    await self._redis.close()
                except Exception:
                    pass
            self._redis = None
            return None

    def _allow_local(self, key: str) -> bool:
        now_value = time.monotonic()
        window = self._local_events[key]
        cutoff = now_value - self.window_seconds
        while window and window[0] <= cutoff:
            window.popleft()
        if len(window) >= self.limit:
            return False
        window.append(now_value)
        if len(self._local_events) > 10_000:
            self._local_events = defaultdict(deque, {item_key: events for item_key, events in self._local_events.items() if events})
        return True

    async def close(self) -> None:
        if self._redis is not None:
            try:
                await self._redis.close()
            finally:
                self._redis = None
