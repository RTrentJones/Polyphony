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
    # A judge reached at its own OpenAI-compatible URL (a separate relay queue
    # answered by a separate agent): overrides EVAL_JUDGE_PROVIDER entirely, so
    # the model under test never grades itself through the same queue.
    judge_base_url: str | None = None
    judge_label: str | None = None
    # The model relay's archive (evals/relay_llm.py `answered/`), read by the
    # prompt-contract grader after the steps run. None = no contract check.
    relay_archive: str | None = None

    @property
    def app_provider(self) -> str:
        return os.getenv("LLM_PROVIDER", "gemini")

    @property
    def judge_is_self(self) -> bool:
        """True when the judge is the same provider as the model under test —
        a self-preference risk worth surfacing in the report. A judge at its own
        base URL is separate unless that URL is the app's own model endpoint."""
        if self.judge_base_url:
            app_url = os.getenv("EVAL_APP_LLM_BASE_URL", "")
            return bool(app_url) and app_url.rstrip("/") == self.judge_base_url
        return self.judge_provider == self.app_provider


# The eval registers its users under this domain (evals/harness/client.py).
EVAL_USER_DOMAIN = "@polyphony.eval"


def generator_provider() -> str:
    """The provider that writes the eval's prose in this run, as the app's
    provider_for routes it: Anthropic when ANTHROPIC_USERS covers the eval's
    users and its key is set (evals.yml claude=yes, claude_generate=yes), else
    the app's LLM_PROVIDER (SPEC-95: claude_generate=no keeps it there)."""
    raw = os.getenv("ANTHROPIC_USERS", "")
    users = {e.strip().lower() for e in raw.split(",") if e.strip()}
    covered = any(u == EVAL_USER_DOMAIN or u.endswith(EVAL_USER_DOMAIN) for u in users)
    if covered and os.getenv("ANTHROPIC_API_KEY"):
        return "anthropic"
    return os.getenv("LLM_PROVIDER", "gemini")


def generator() -> dict:
    """{provider, model} that generate in a booted run, from its config: the
    app provider's LLM_MODEL (else its default), or Anthropic's default."""
    from app.llm.providers import PROVIDERS

    pid = generator_provider()
    app = os.getenv("LLM_PROVIDER", "gemini")
    model = os.getenv("LLM_MODEL") if pid == app else None
    row = PROVIDERS.get(pid)
    return {
        "provider": pid,
        "model": model or (row.default_model if row else None),
        "source": "config",
    }


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
        judge_base_url=(os.getenv("EVAL_JUDGE_BASE_URL") or "").rstrip("/") or None,
        judge_label=os.getenv("EVAL_JUDGE_LABEL") or None,
        relay_archive=_relay_archive(),
    )


def _relay_archive() -> str | None:
    """EVAL_RELAY_ARCHIVE (an `answered/` dir), else EVAL_RELAY_DIR/answered."""
    explicit = os.getenv("EVAL_RELAY_ARCHIVE")
    if explicit:
        return explicit
    relay_dir = os.getenv("EVAL_RELAY_DIR")
    return os.path.join(relay_dir, "answered") if relay_dir else None
