"""Durable Postgres-backed background jobs.

- repository: enqueue/claim/succeed/fail/reap primitives over an AsyncSession.
- handlers: dispatch table mapping job kind -> workflow entrypoint.
- worker: single-consumer poll loop started from the app lifespan.
"""
