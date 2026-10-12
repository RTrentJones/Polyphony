# SPEC-05 — Rate limits keyed on the real client and the user

**Closes**: F7 · **Size**: S · **Depends on**: —

## Why

Both slowapi limiters key on `get_remote_address` (`app/main.py:128`, `app/api/auth.py:36`), and
uvicorn runs without trusting proxy headers. Behind `cloudflared`, every request appears to come
from the tunnel, so the 5/minute login limit and the 3/hour register limit are global (one script
can lock every user out of login), and the 60/minute default is one bucket shared by all users and
their polling tabs.

## Outcome

Unauthenticated routes are limited per real client IP, taken from Cloudflare's `CF-Connecting-IP`
only when the request came through the tunnel. Authenticated routes are limited per user. Polling
reads do not compete with writes.

## Scope

**In**: limiter key functions, uvicorn proxy settings, per-route limits for auth, polling and
generation endpoints.
**Out**: Cloudflare-side WAF rules (worth adding in the wrapper repo's infra, tracked there).

## Design

1. **Confirm first.** Log `request.client.host` and the presence of `CF-Connecting-IP` for a day in
   production (one structured log field on `log_request_start`) and record the observed values in
   the PR. If the tunnel is not the peer, adjust step 2 accordingly.
2. **Client IP.** A key function `client_ip(request)` returns `CF-Connecting-IP` when the peer
   address is in a configured `TRUSTED_PROXY_CIDRS` (default: loopback and the Docker bridge),
   else the peer address. Never trust the header from an untrusted peer.
3. **User key.** A key function `user_or_ip(request)` returns `user:<id>` when a valid bearer token
   is present (decode without a DB hit; the dependency still authorises), else `ip:<client_ip>`.
   The default limits use it.
4. **Route classes.** Auth: `login` 5/minute per IP and 20/hour per email; `register` 3/hour per
   IP. Polling GETs (`/books/{id}/plans`, `/books/{id}/scenes`, `/sources/{id}`, extraction runs):
   120/minute per user. LLM-enqueueing POSTs: 10/minute per user (the budget is the real control;
   this stops runaway clients).
5. **Response.** 429s carry `Retry-After`; the frontend's polling backs off on 429.

## Acceptance

1. Two requests with different `CF-Connecting-IP` values from a trusted peer get separate login
   buckets; the same header from an untrusted peer is ignored. *(unit with TestClient and a patched
   client address)*
2. Two authenticated users each get their own default bucket. *(unit)*
3. Six failed logins for one email from six IPs within an hour trip the per-email limit only for
   that email. *(unit)*
4. Polling at 1 request/second for 60 seconds does not consume the generation route's limit.
   *(unit)*

## Docs

README "Auth" section gains a "Rate limits" paragraph. Wrapper repo: note the trusted-proxy
setting in the Polyphony deploy docs.
