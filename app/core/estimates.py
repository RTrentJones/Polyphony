"""Cost estimates for every LLM-spending action (SPEC-12, docs/BRD.md R7.5).

One module owns the arithmetic, so the number an author sees before clicking is
the number the enqueue endpoint enforces. Each estimator returns calls, tokens
and the mode that will actually run; `assess` adds what the author can afford
today (tokens left in their rolling budget, requests left in the provider's
day) and whether the action is allowed.

Estimates are deliberately upper-bound-ish: refusing a job that would have
fitted is cheaper for the author than a job that half-writes and pauses. A
batch of scenes is not one job: it queues the scenes that fit (`affordable`).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from itertools import accumulate
from typing import Optional
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.budget import estimate_tokens, remaining_budget_24h

# Answer budgets per call family (tokens), mirroring each generator's max_tokens.
_PLAN_BEATS_OUT = 1200
_BEAT_PROSE_OUT_PER_WORD = 2.2
_OUTLINE_OUT = {"skeleton": 2048, "chapters": 4096, "beats": 4096}
_CONTINUITY_OUT = 3000
_ENSEMBLE_OUT = {"proposal": 600, "narration_per_word": 2.0, "review": 1024}
# Scenes plan 3-5 beats; estimate the upper end.
_EXPECTED_BEATS = 5
# Character agents see only their own bible + brief + chunks (~800 tokens).
_AGENT_CONTEXT = 800


@dataclass
class Estimate:
    action: str
    mode: str
    calls: int
    input_tokens: int
    output_tokens: int
    degraded_from: Optional[str] = None
    notes: list[str] = field(default_factory=list)

    @property
    def tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def as_dict(self) -> dict:
        d = asdict(self)
        d["tokens"] = self.tokens
        d["usd"] = _usd(self.input_tokens, self.output_tokens)
        return d


def _usd(tokens_in: int, tokens_out: int) -> Optional[float]:
    from app.llm.prices import cost_usd
    from app.llm.providers import active_provider, resolve_model

    try:
        model = resolve_model(active_provider(), fast=False)
    except Exception:  # unknown provider: no price to show
        return None
    cost = cost_usd(model, tokens_in, tokens_out)
    return None if cost is None else round(cost, 4)


def scene(
    *,
    canon_chars: int,
    cast_context_chars: int,
    target_words: int,
    mode: str = "prose",
    cast_size: int = 2,
) -> Estimate:
    """A scene: one planner call, then per beat either one prose call (prose)
    or agents + narrator + editor per round (ensemble)."""
    from app.llm.tier import get_tier

    tier = get_tier()
    requested = mode
    degraded = None
    if mode == "ensemble" and not tier.allow_ensemble:
        mode, degraded = "prose", "ensemble"
    canon = estimate_tokens(canon_chars)
    cast = estimate_tokens(cast_context_chars)
    beats = _EXPECTED_BEATS
    per_beat_words = max(120, target_words // beats)
    calls = 1
    tokens_in = canon + 600
    tokens_out = _PLAN_BEATS_OUT // 2
    if mode == "prose":
        calls += beats
        tokens_in += beats * (canon + cast + 1200)
        tokens_out += int(beats * per_beat_words * _BEAT_PROSE_OUT_PER_WORD)
    else:
        agents = min(cast_size, tier.max_agents)
        rounds = tier.max_rounds
        per_round = agents + 2
        calls += beats * per_round * rounds
        tokens_in += (
            beats * rounds * (agents * (_AGENT_CONTEXT + 400) + 2 * (canon + 1500))
        )
        tokens_out += (
            beats
            * rounds
            * (
                agents * _ENSEMBLE_OUT["proposal"] // 2
                + int(per_beat_words * _ENSEMBLE_OUT["narration_per_word"])
                + _ENSEMBLE_OUT["review"] // 2
            )
        )
    est = Estimate("scene", mode, calls, tokens_in, tokens_out, degraded)
    if degraded:
        est.notes.append(
            f"{requested} is not available on the {tier.name} tier; prose mode will run."
        )
    return est


def revise(
    *,
    canon_chars: int,
    cast_context_chars: int,
    draft_chars: int,
    target_chars: int,
) -> Estimate:
    """An AI revision of a scene span (SPEC-19): one call that reads the whole
    canon, the scene's cast and the WHOLE draft (the span plus its read-only
    surroundings) and writes the span again. The answer budget is the revise
    call's own max_tokens (`revise_max_tokens`)."""
    tokens_in = (
        estimate_tokens(canon_chars)
        + estimate_tokens(cast_context_chars)
        + estimate_tokens(draft_chars)
        + 600  # instructions + fences
    )
    return Estimate(
        "revise", "single", 1, tokens_in, revise_max_tokens(target_chars) // 2
    )


def revise_max_tokens(target_chars: int) -> int:
    """The revise call's answer cap: room to rewrite the span at up to ~3x its
    length (an instruction may ask to expand it), never below a paragraph."""
    return max(600, estimate_tokens(target_chars) * 3)


def outline(*, canon_chars: int, source_chars: int, staged: bool = True) -> Estimate:
    """Staged: skeleton + chapters + ~4 act batches (+ one possible repair of
    chapters and beats), then, with sources, one check of the outline against
    them (SPEC-73). Single: one call."""
    canon = estimate_tokens(canon_chars)
    if not staged:
        return Estimate("outline", "single", 1, canon + 800, 4096 // 2)
    calls = 1 + 1 + 4
    tokens_in = calls * (canon + 800)
    tokens_out = (
        _OUTLINE_OUT["skeleton"] + _OUTLINE_OUT["chapters"] + 4 * _OUTLINE_OUT["beats"]
    ) // 2
    if source_chars:
        calls += 1  # the outline in, at most what its stages wrote
        tokens_in += estimate_tokens(source_chars) + tokens_out + 800
        tokens_out += _CONTINUITY_OUT // 3
    est = Estimate("outline", "staged", calls, tokens_in, tokens_out)
    est.notes.append(
        "If a principal character is missing, the chapters and beats are "
        "regenerated once (up to 5 more calls)."
    )
    return est


def continuity(*, fact_sheet_chars: int, prose_words: int) -> Estimate:
    from app.planning.continuity import CHUNK_WORDS

    chunks = max(1, -(-prose_words // CHUNK_WORDS))
    per_chunk_in = estimate_tokens(fact_sheet_chars) + int(CHUNK_WORDS * 1.4) + 800
    return Estimate(
        "continuity",
        "map",
        chunks,
        chunks * per_chunk_in,
        chunks * _CONTINUITY_OUT // 3,
    )


def extraction(*, text: str) -> Estimate:
    from app.parsing.canon_extractor import plan_estimate

    p = plan_estimate(text)
    total = int(p["estimated_tokens"])
    est = Estimate(
        "extraction", "windowed", int(p["requests"]), total * 4 // 5, total // 5
    )
    est.notes.append(f"{p['windows']} window(s), {p['reduce_calls']} reduce call(s).")
    return est


def affordable(per_scene: list[Estimate], remaining: int) -> int:
    """How many of these scenes, in order, fit `remaining` tokens: the batch
    queues that prefix and defers the rest (SPEC-34). The ONE partial rule."""
    return sum(
        1 for spent in accumulate(e.tokens for e in per_scene) if spent <= remaining
    )


async def requests_left_today() -> Optional[int]:
    """Provider requests left today for the default model, or None if untracked."""
    from app.core.database import get_async_session
    from app.llm.ledger import used_today
    from app.llm.providers import active_provider, resolve_model
    from app.llm.tier import effective_rpd

    provider = active_provider()
    ceiling = effective_rpd(provider, fast=False)
    if not ceiling:
        return None
    try:
        async with get_async_session() as session:
            used = await used_today(
                session,
                provider.id,
                resolve_model(provider, False),
                provider.rpd_reset_tz,
            )
    except Exception:
        return None
    return max(0, ceiling - used)


async def assess(db: AsyncSession, user_id: UUID, est: Estimate) -> dict:
    """The estimate plus what the author can afford, and whether it is allowed."""
    remaining = await remaining_budget_24h(db, user_id)
    requests = await requests_left_today()
    out = est.as_dict()
    out["remaining_tokens_24h"] = remaining
    out["remaining_requests_today"] = requests
    out["share_of_remaining"] = round(est.tokens / remaining, 4) if remaining else None
    reason = None
    if est.tokens > remaining:
        reason = f"Needs about {est.tokens:,} tokens; {remaining:,} remain in today's budget."
    elif requests is not None and est.calls > requests:
        msg = (
            f"Needs about {est.calls} model requests; {requests} remain in the "
            "provider's day."
        )
        if est.mode == "ensemble":
            # An ensemble scene is many calls; refuse rather than pause mid-loop.
            reason = msg
        else:
            out["notes"] = [*out["notes"], msg + " It will pause and resume tomorrow."]
    out["allowed"] = reason is None
    out["reason"] = reason
    return out
