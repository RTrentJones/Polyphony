"""Eval harness configuration — all from env so nothing secret is committed.

The harness drives a RUNNING Polyphony (local preview or a deployed URL); it
never imports the app's request handlers. The judge is pluggable so it can move
off Gemini once another provider key exists.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class EvalConfig:
    base_url: str
    admin_email: str
    admin_password: str
    judge_provider: str
    judge_model: str | None
    cache_dir: str
    rpm: int  # pace generation to the free-tier request rate

    @property
    def app_provider(self) -> str:
        return os.getenv("LLM_PROVIDER", "gemini")

    @property
    def judge_is_self(self) -> bool:
        """True when the judge is the same provider as the model under test —
        a self-preference risk worth surfacing in the report."""
        return self.judge_provider == self.app_provider


def load() -> EvalConfig:
    return EvalConfig(
        base_url=os.getenv("EVAL_BASE_URL", "http://localhost:8000").rstrip("/"),
        admin_email=os.getenv("EVAL_ADMIN_EMAIL", os.getenv("ADMIN_EMAIL", "")),
        admin_password=os.getenv(
            "EVAL_ADMIN_PASSWORD", os.getenv("ADMIN_PASSWORD", "")
        ),
        # Default judge = Gemini (the only key present today); swap by setting
        # EVAL_JUDGE_PROVIDER=anthropic|groq|openai|xai once that key exists.
        judge_provider=os.getenv("EVAL_JUDGE_PROVIDER", "gemini"),
        judge_model=os.getenv("EVAL_JUDGE_MODEL") or None,
        cache_dir=os.getenv("EVAL_CACHE_DIR", ".eval-cache"),
        rpm=int(os.getenv("EVAL_RPM", "8")),
    )
