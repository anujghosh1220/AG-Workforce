"""Phase 8.5/8.8 — rate limiting abstraction with production diagnostics.

Local dev: in-memory sliding-window limiter (no Redis needed).
Production: Redis-backed when REDIS_URL is configured. If distributed
limiting is REQUIRED (ENVIRONMENT=production or REQUIRE_REDIS_RATE_LIMIT=1)
and Redis is missing/unreachable, requests fail closed (RateLimiterUnavailable
-> HTTP 503). The process-local backend is never silently substituted in
production.

Diagnostics NEVER expose the Redis URL or credentials.
"""
from __future__ import annotations

import os
import threading
import time
from collections import defaultdict, deque


class RateLimitExceeded(Exception):
    def __init__(self, retry_after: int):
        self.retry_after = retry_after


class RateLimiterUnavailable(Exception):
    """Required backend is down — callers should return 503."""


class _MemoryLimiter:
    def __init__(self):
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def hit(self, key: str, limit: int, window: int) -> int:
        now = time.time()
        with self._lock:
            q = self._hits[key]
            while q and q[0] <= now - window:
                q.popleft()
            if len(q) >= limit:
                return max(1, int(q[0] + window - now))
            q.append(now)
            return 0


class _RedisLimiter:
    """Thin Redis wrapper; only used when REDIS_URL is set and redis-py
    is installed. Uses INCR + EXPIRE fixed windows."""

    def __init__(self, url: str):
        import redis  # optional dependency
        self._r = redis.Redis.from_url(url, socket_timeout=2,
                                       socket_connect_timeout=2)

    def hit(self, key: str, limit: int, window: int) -> int:
        pipe_count = self._r.incr(f"rl:{key}")
        if pipe_count == 1:
            self._r.expire(f"rl:{key}", window)
        if pipe_count > limit:
            ttl = self._r.ttl(f"rl:{key}")
            return max(1, int(ttl or window))
        return 0

    def ping(self) -> bool:
        try:
            return bool(self._r.ping())
        except Exception:
            return False


_backend = None
_backend_name = "uninitialized"
_backend_healthy: bool | None = None
_health_checked_at = 0.0
_HEALTH_CACHE_SECONDS = 5.0


def _required() -> bool:
    return (os.environ.get("REQUIRE_REDIS_RATE_LIMIT") == "1"
            or os.environ.get("ENVIRONMENT") == "production")


def _get_backend():
    """Select backend once per process.

    - No REDIS_URL: memory (dev) — or RuntimeError when required.
    - REDIS_URL set: always select redis when required; in dev fall back to
      memory only if the initial ping fails (reported as degraded).
    """
    global _backend, _backend_name, _backend_healthy
    if _backend is not None:
        return _backend, _backend_name
    url = os.environ.get("REDIS_URL")
    required = _required()
    if required and not url:
        raise RuntimeError(
            "Rate limiting requires REDIS_URL in production "
            "(or set ENVIRONMENT!=production / REQUIRE_REDIS_RATE_LIMIT=0)")
    if url:
        try:
            candidate = _RedisLimiter(url)
            reachable = candidate.ping()
        except Exception:
            candidate, reachable = None, False
        if reachable:
            _backend, _backend_name, _backend_healthy = candidate, "redis", True
        elif required:
            # Keep the redis backend so hit() fails closed per-request rather
            # than silently downgrading to process-local memory.
            _backend = _RedisLimiter(url)
            _backend_name, _backend_healthy = "redis", False
        else:
            _backend, _backend_name, _backend_healthy = _MemoryLimiter(), "memory", False
    else:
        _backend, _backend_name, _backend_healthy = _MemoryLimiter(), "memory", True
    return _backend, _backend_name


def hit(key: str, limit: int, window: int) -> int:
    """Returns retry-after seconds; 0 means request allowed."""
    global _backend_healthy
    backend, name = _get_backend()
    try:
        return backend.hit(key, limit, window)
    except Exception as exc:
        if name == "redis":
            _backend_healthy = False
            if _required():
                raise RateLimiterUnavailable("redis rate limiter unavailable") from exc
            # dev: degrade to memory for this call only
            return _MemoryLimiter().hit(key, limit, window)
        raise


def _redis_health() -> dict:
    """Live reachability check (cached briefly). Never exposes the URL."""
    global _backend_healthy, _health_checked_at
    url = os.environ.get("REDIS_URL")
    out = {
        "configured": bool(url),
        "reachable": None,
        "error": None,
    }
    if not url:
        return out
    now = time.time()
    if now - _health_checked_at < _HEALTH_CACHE_SECONDS and _backend_healthy is not None:
        out["reachable"] = _backend_healthy
        return out
    try:
        backend, name = _get_backend()
        if name == "redis" and hasattr(backend, "ping"):
            out["reachable"] = bool(backend.ping())
        else:
            out["reachable"] = False
    except Exception as exc:
        out["reachable"] = False
        out["error"] = type(exc).__name__
    _backend_healthy = out["reachable"]
    _health_checked_at = now
    return out


def status() -> dict:
    _, name = _get_backend()
    required = _required()
    redis = _redis_health()
    degraded = (name == "redis" and redis["reachable"] is False) or \
               (name == "memory" and redis["configured"] and required)
    return {
        "backend": name,
        "distributed": name == "redis",
        "distributed_required": required,
        "redis_url_configured": redis["configured"],
        "redis_reachable": redis["reachable"],
        "redis_checked": redis["reachable"] is not None,
        "degraded": degraded,
        "config_valid": not (required and not redis["configured"]),
        "environment": os.environ.get("ENVIRONMENT", "development"),
    }


def reset_for_tests():
    global _backend, _backend_name, _backend_healthy, _health_checked_at
    _backend = None
    _backend_name = "uninitialized"
    _backend_healthy = None
    _health_checked_at = 0.0
