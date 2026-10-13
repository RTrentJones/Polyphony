"""Request rate limits keyed on the real client and the user (SPEC-05, F7).

Production sits behind a Cloudflare tunnel, so the TCP peer is `cloudflared`,
not the browser. Keying on the peer made every limit global: one script could
hold the 5/minute login bucket for everyone. So:

- `client_ip` takes Cloudflare's `CF-Connecting-IP` only when the peer is a
  trusted proxy (`TRUSTED_PROXY_CIDRS`); from anyone else the header is a
  client-supplied string and is ignored.
- `user_or_ip` keys authenticated traffic on the user id from the bearer JWT
  (signature-checked, no DB hit; the route's auth dependency still decides
  whether the request is allowed) and falls back to the client IP.
- Route classes (`ROUTE_CLASSES`) give polling reads their own generous bucket
  and LLM-enqueueing writes a tight one, so a tab polling a running job never
  starves the author's next click, and a runaway client cannot queue a hundred
  generations (the token budget is the real control; this is the seatbelt).

One in-memory store backs every limit (one container; ADR-001 §4), so
`limiter.reset()` clears them all — the test suite does that per test.
"""

from __future__ import annotations

import ipaddress
import math
import time
from dataclasses import dataclass
from functools import lru_cache

from fastapi import Request
from fastapi.responses import JSONResponse
from jose import JWTError, jwt
from limits import RateLimitItem, parse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.routing import Match

from app.core.config import settings

CF_HEADER = "cf-connecting-ip"


# --- key functions -----------------------------------------------------------


@lru_cache(maxsize=8)
def _trusted_networks(spec: str) -> tuple:
    nets = []
    for part in spec.split(","):
        part = part.strip()
        if part:
            nets.append(ipaddress.ip_network(part, strict=False))
    return tuple(nets)


def _is_trusted_peer(host: str | None) -> bool:
    if not host:
        return False
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False
    return any(addr in net for net in _trusted_networks(settings.TRUSTED_PROXY_CIDRS))


def peer_address(request: Request) -> str:
    return request.client.host if request.client else ""


def client_ip(request: Request) -> str:
    """The real client address: `CF-Connecting-IP` from a trusted peer, else
    the peer itself. A malformed header is ignored rather than trusted."""
    peer = peer_address(request)
    header = request.headers.get(CF_HEADER, "").strip()
    if header and _is_trusted_peer(peer):
        try:
            return str(ipaddress.ip_address(header))
        except ValueError:
            pass
    return peer or "unknown"


def _bearer_user_id(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    scheme, _, token = auth.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    try:
        payload = jwt.decode(
            token.strip(), settings.SECRET_KEY, algorithms=[settings.ALGORITHM]
        )
    except JWTError:
        return None
    sub = payload.get("sub")
    return str(sub) if sub else None


def user_or_ip(request: Request) -> str:
    """`user:<id>` for a validly-signed bearer token, else `ip:<client_ip>`.

    Decoding without a DB hit is safe for *keying*: a forged token fails the
    signature check and falls back to the IP, and a deleted/deactivated user's
    request is still refused by the route's auth dependency."""
    uid = _bearer_user_id(request)
    return f"user:{uid}" if uid else f"ip:{client_ip(request)}"


# --- the limiter -------------------------------------------------------------

limiter = Limiter(
    key_func=user_or_ip,
    default_limits=[
        f"{settings.RATE_LIMIT_PER_MINUTE}/minute",
        f"{settings.RATE_LIMIT_PER_HOUR}/hour",
    ],
    storage_uri="memory://",
)


def retry_after_seconds(item: RateLimitItem, *identifiers: str) -> int:
    """Seconds until the bucket identified by `identifiers` has room again."""
    try:
        reset_at, _remaining = limiter.limiter.get_window_stats(item, *identifiers)
    except Exception:  # storage hiccup: the window length is a safe answer
        return int(item.get_expiry())
    return max(1, math.ceil(reset_at - time.time()))


def too_many_requests(detail: str, retry_after: int) -> JSONResponse:
    return JSONResponse(
        {"detail": f"Rate limit exceeded: {detail}", "retry_after": retry_after},
        status_code=429,
        headers={"Retry-After": str(retry_after)},
    )


def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded):
    """slowapi's own handler only sets Retry-After when its header injection is
    enabled for every response; we want it on 429s only, always. Synchronous on
    purpose: SlowAPIMiddleware swaps an async handler for its own default."""
    current = getattr(request.state, "view_rate_limit", None)
    if current is not None:
        retry_after = retry_after_seconds(current[0], *current[1])
    else:
        retry_after = 60
    return too_many_requests(str(exc.detail), retry_after)


# --- per-email login limit ---------------------------------------------------

# The per-email limit exists to stop a botnet guessing one account's password
# from many IPs. Its cost is that the same botnet can spend the budget to lock
# the real owner out. Two things bound that (SPEC-05, "Per-email lockout"):
#
# - Trusted addresses. An account's recent successful-login addresses are
#   exempt (bounded per account), so an owner on a known network is never
#   locked out.
# - A DECAYING lockout. Spending the failure budget (LOGIN_EMAIL_RATE_LIMIT)
#   starts a short lockout that doubles per repeated breach and caps low
#   (LOGIN_EMAIL_LOCKOUT_SECONDS .. LOGIN_EMAIL_LOCKOUT_MAX_SECONDS, minutes),
#   then the budget refills. The breach count forgets after
#   LOGIN_EMAIL_BREACH_RESET_SECONDS without a breach. A lockout therefore never
#   outlasts the cap; to keep an owner out the attacker must keep spending fresh
#   budgets, each of which the per-IP limit (5/minute) throttles.
#
# The per-IP limit is the main brute-force control; this one only caps how many
# guesses a distributed attacker gets per account. In-process, like every limit
# here (ADR-001's single container).
_TRUSTED_LOGIN_IPS: dict[str, list[str]] = {}
_TRUSTED_PER_ACCOUNT = 5


@dataclass
class _Lockout:
    until: float = 0.0
    breaches: int = 0
    last_breach: float = 0.0


_LOGIN_LOCKOUTS: dict[str, _Lockout] = {}


def _email_key(email: str) -> str:
    return email.strip().lower()


def lockout_seconds(breaches: int) -> int:
    """The lockout for the n-th breach (1-based): doubling, capped low."""
    base = max(1, settings.LOGIN_EMAIL_LOCKOUT_SECONDS)
    cap = max(base, settings.LOGIN_EMAIL_LOCKOUT_MAX_SECONDS)
    return min(cap, base * 2 ** max(0, min(breaches - 1, 30)))


def reset_login_state() -> None:
    """Forget trusted addresses and lockouts (the test suite does this per test)."""
    _TRUSTED_LOGIN_IPS.clear()
    _LOGIN_LOCKOUTS.clear()


def login_email_check(email: str, ip: str | None = None) -> int | None:
    """Retry-After seconds if this email is in a lockout, else None. Read-only:
    a correct password never spends the budget, and the lockout never applies
    from an address this account signed in from."""
    key = _email_key(email)
    if ip and ip in _TRUSTED_LOGIN_IPS.get(key, ()):
        return None
    lock = _LOGIN_LOCKOUTS.get(key)
    now = time.time()
    if lock is None or lock.until <= now:
        return None
    return max(1, math.ceil(lock.until - now))


def login_email_record_failure(email: str) -> None:
    """Spend one failed attempt; spending the whole budget starts a lockout."""
    key = _email_key(email)
    item = parse(settings.LOGIN_EMAIL_RATE_LIMIT)
    limiter.limiter.hit(item, "login-email", key)
    if limiter.limiter.test(item, "login-email", key):
        return
    now = time.time()
    lock = _LOGIN_LOCKOUTS.setdefault(key, _Lockout())
    if now - lock.last_breach > settings.LOGIN_EMAIL_BREACH_RESET_SECONDS:
        lock.breaches = 0  # quiet long enough: start the doubling over
    lock.breaches += 1
    lock.last_breach = now
    lock.until = now + lockout_seconds(lock.breaches)
    limiter.limiter.clear(item, "login-email", key)  # a fresh budget afterwards


def login_email_record_success(email: str, ip: str | None) -> None:
    # Deliberately leaves the budget and any lockout alone: an owner signing
    # in from a trusted address must not hand the attacker a fresh budget.
    if not ip:
        return
    key = _email_key(email)
    ips = [i for i in _TRUSTED_LOGIN_IPS.get(key, []) if i != ip]
    _TRUSTED_LOGIN_IPS[key] = [ip, *ips][:_TRUSTED_PER_ACCOUNT]


# --- route classes -----------------------------------------------------------

# (method, route template) -> class. Templates are FastAPI's, prefix included.
# Polling: every GET the UI (or the eval harness) polls while a job runs.
# LLM: every POST that enqueues or makes a model call.
ROUTE_CLASSES: dict[tuple[str, str], str] = {
    ("GET", "/api/v1/books/{book_id}/plans"): "poll",
    ("GET", "/api/v1/books/{book_id}/scenes"): "poll",
    ("GET", "/api/v1/books/{book_id}/continuity"): "poll",
    ("GET", "/api/v1/books/{book_id}/extractions/{run_id}"): "poll",
    ("GET", "/api/v1/scenes/{scene_id}"): "poll",
    ("GET", "/api/v1/sources/{source_id}"): "poll",
    ("GET", "/api/v1/sources/{source_id}/characters"): "poll",
    ("GET", "/api/v1/jobs/{job_id}"): "poll",  # useJob polls it (SPEC-07)
    ("POST", "/api/v1/books/chapters/{chapter_id}/scenes/generate"): "llm",
    # Generates every planned scene of a chapter, as a chain (SPEC-10).
    ("POST", "/api/v1/books/chapters/{chapter_id}/scenes/generate-planned"): "llm",
    ("POST", "/api/v1/books/{book_id}/plans/generate"): "llm",
    # Confirming a premise enqueues the rest of the outline (SPEC-10).
    ("POST", "/api/v1/books/{book_id}/plans/{plan_id}/confirm-premise"): "llm",
    ("POST", "/api/v1/books/{book_id}/continuity"): "llm",
    ("POST", "/api/v1/books/{book_id}/sources/{source_id}/extract"): "llm",
    ("POST", "/api/v1/sources/upload"): "llm",
    ("POST", "/api/v1/sources/paste"): "llm",
    ("POST", "/api/v1/sources/{source_id}/process"): "llm",
    # Editing a source's text re-runs extraction (a title-only edit is rare
    # enough that sharing the tight bucket costs nothing).
    ("PATCH", "/api/v1/sources/{source_id}"): "llm",
    ("POST", "/api/v1/characters/{character_id}/test-dialogue"): "llm",
}


_CLASSIFIED_PREFIX = "/api/v1/"
assert all(path.startswith(_CLASSIFIED_PREFIX) for _, path in ROUTE_CLASSES)


def class_limit(cls: str) -> RateLimitItem:
    return parse(settings.RATE_LIMIT_POLL if cls == "poll" else settings.RATE_LIMIT_LLM)


def _route_template(request: Request) -> str | None:
    """Resolve the matched route's template before routing runs (slowapi's
    middleware does the same scan to find its handler). Every classified route
    is under /api/v1/, so static pages and assets skip the scan."""
    if not request.url.path.startswith(_CLASSIFIED_PREFIX):
        return None
    for route in request.app.routes:
        match, _ = route.matches(request.scope)
        if match == Match.FULL and hasattr(route, "endpoint"):
            return getattr(route, "path", None)
    return None


class RouteClassRateLimitMiddleware(BaseHTTPMiddleware):
    """Applies the poll / llm class limits, keyed per user, and exempts those
    routes from the default per-route limits (slowapi skips a request whose
    `_rate_limiting_complete` flag is set). Must wrap SlowAPIMiddleware."""

    async def dispatch(self, request: Request, call_next):
        if limiter.enabled:
            template = _route_template(request)
            cls = ROUTE_CLASSES.get((request.method, template)) if template else None
            if cls is not None:
                item = class_limit(cls)
                key = user_or_ip(request)
                if not limiter.limiter.hit(item, "class", cls, key):
                    return too_many_requests(
                        f"{item} ({cls})", retry_after_seconds(item, "class", cls, key)
                    )
                request.state._rate_limiting_complete = True
        return await call_next(request)
