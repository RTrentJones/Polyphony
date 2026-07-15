"""Step registry + shared context.

Every eval step is a coroutine `run(ctx) -> dict` registered by name. Adding a
step is a single `@step("name", needs_api=...)` decorator — the runner discovers
steps from the registry, so nothing else changes. `needs_api` lets the runner
skip (not fail) LLM/API steps when no server or admin creds are configured.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

from evals.harness.cache import Cache
from evals.harness.client import PolyphonyClient
from evals.harness.judge import Judge


@dataclass
class StepContext:
    """Everything a step might need — passed uniformly so step signatures don't
    drift as steps are added."""

    book: str
    corpus_text: str
    ground_truth: dict
    cache: Cache
    judge: Judge
    client: Optional[PolyphonyClient] = None  # None for embedding-only steps
    # Per-pass salt under --repeat (pass index; "" for a single pass). Upload
    # steps mix it into content/title so a re-run gets a distinct content_hash
    # instead of 409-ing on the per-user manuscript dedup.
    pass_salt: str = ""


@dataclass
class Step:
    name: str
    run: Callable[[StepContext], Awaitable[dict]]
    needs_api: bool  # True → needs the running server + admin creds


_REGISTRY: dict[str, Step] = {}


def step(name: str, *, needs_api: bool):
    """Register a step under `name`. Order of registration = default run order."""

    def deco(fn: Callable[[StepContext], Awaitable[dict]]) -> Callable:
        if name in _REGISTRY:
            raise ValueError(f"duplicate eval step {name!r}")
        _REGISTRY[name] = Step(name=name, run=fn, needs_api=needs_api)
        return fn

    return deco


def all_steps() -> list[str]:
    return list(_REGISTRY)


def get_step(name: str) -> Step:
    if name not in _REGISTRY:
        raise KeyError(f"unknown eval step {name!r} (known: {list(_REGISTRY)})")
    return _REGISTRY[name]
