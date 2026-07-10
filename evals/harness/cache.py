"""Generation cache — free-tier discipline.

Every model-spending call (a generated scene, an outline, a test-dialogue line)
is cached on disk keyed by (case, prompt, app version). Re-running the suite to
re-grade never re-generates, so grading iterations are free. Keyed by the app's
/__version sha so a new build re-generates rather than grading stale output.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


class Cache:
    def __init__(self, cache_dir: str, app_sha: str):
        self._dir = Path(cache_dir) / (app_sha or "unknown")[:12]
        self._dir.mkdir(parents=True, exist_ok=True)

    def _path(self, namespace: str, key: str) -> Path:
        h = hashlib.sha256(key.encode("utf-8")).hexdigest()[:20]
        return self._dir / f"{namespace}-{h}.json"

    def get(self, namespace: str, key: str):
        p = self._path(namespace, key)
        if p.exists():
            return json.loads(p.read_text())
        return None

    def put(self, namespace: str, key: str, value) -> None:
        self._path(namespace, key).write_text(json.dumps(value, ensure_ascii=False))

    async def memo(self, namespace: str, key: str, produce):
        """Return cached value for key, else await produce() and cache it."""
        hit = self.get(namespace, key)
        if hit is not None:
            return hit
        value = await produce()
        self.put(namespace, key, value)
        return value
