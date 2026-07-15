"""Pluggable LLM judge for the rubric-scored evals (voice fidelity, outline
coherence, prose quality).

Grades with a provider chosen by config (default Gemini today; swap to Anthropic
etc. once that key exists) — ideally NOT the model under test. Returns a 0..1
score + explanation, parsed robustly. Reuses the app's own LLMClient so the
call goes through the same pacing/retry/breaker as production.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from evals.harness.config import EvalConfig

_SYSTEM = (
    "You are a strict, fair literary evaluator. Score the response against the "
    "rubric on a scale from 0.0 (fails the rubric entirely) to 1.0 (fully meets "
    'it). Reply ONLY with JSON: {"score": <float 0..1>, "explanation": <one sentence>}.'
)


@dataclass
class Judgment:
    score: float
    explanation: str
    judge_provider: str
    judge_model: str


def _clamp01(x) -> float:
    try:
        return max(0.0, min(1.0, float(x)))
    except (TypeError, ValueError):
        return 0.0


def _parse(text: str) -> tuple[float, str]:
    """Extract {score, explanation} from a judge reply; tolerant of fences/prose."""
    import re

    t = (text or "").strip().replace("```json", "").replace("```", "")
    start, end = t.find("{"), t.rfind("}")
    if start != -1 and end > start:
        try:
            obj = json.loads(t[start : end + 1])
            return _clamp01(obj.get("score")), str(obj.get("explanation", ""))[:300]
        except json.JSONDecodeError:
            pass
    # last resort: pull the first float that looks like a score.
    m = re.search(r'score"?\s*[:=]\s*([01](?:\.\d+)?)', t)
    if m:
        return _clamp01(m.group(1)), "recovered score from unstructured reply"
    return 0.0, "unparseable judge reply"


class Judge:
    def __init__(self, cfg: EvalConfig):
        self._cfg = cfg
        self._client = None
        # Resolved eagerly so the report records the judge that will ACTUALLY
        # grade (post-fallback), not just the requested one.
        self.provider_id, self.fell_back = self._resolve()
        primary = self._cfg.judge_provider.split(",")[0].strip()
        # An explicit EVAL_JUDGE_MODEL names a model for the PRIMARY chain
        # provider only — applying it to a different vendor would 404.
        self.model_override = (
            self._cfg.judge_model
            if (self.provider_id == primary and not self.fell_back)
            else None
        )

    def _resolve(self) -> tuple[str, bool]:
        """Pick the judge provider fail-soft. EVAL_JUDGE_PROVIDER may be a
        comma-separated preference chain ("groq,openrouter"): the first
        provider whose key is set grades. If none in the chain has a key, fall
        back to the app's own provider (self-grading beats no grading — CI
        stays green while judge keys haven't been minted yet)."""
        from app.llm.providers import PROVIDERS, provider_api_key

        chain = [p.strip() for p in self._cfg.judge_provider.split(",") if p.strip()]
        if not chain:
            raise RuntimeError("empty judge provider")
        for name in chain:  # typo protection before any key check
            if PROVIDERS.get(name) is None:
                raise RuntimeError(f"unknown judge provider {name!r}")
        for i, name in enumerate(chain):
            if provider_api_key(PROVIDERS[name]):
                if i > 0:
                    print(
                        f"eval judge: {chain[0]!r} has no key — using the next "
                        f"provider in the chain, {name!r}"
                    )
                # fell_back stays False within the chain: any chain member is a
                # deliberate, non-self judge choice.
                return name, False
        fallback = self._cfg.app_provider
        if fallback not in chain and PROVIDERS.get(fallback):
            print(
                f"eval judge: no key set for any of {chain} — falling back to "
                f"the app provider {fallback!r} (self-grading; scores may show "
                "self-preference bias)"
            )
            return fallback, True
        return chain[0], False

    @property
    def is_self(self) -> bool:
        """True when the EFFECTIVE judge is the model under test's provider."""
        return self.provider_id == self._cfg.app_provider

    def _ensure(self):
        if self._client is None:
            from app.llm.client import LLMClient
            from app.llm.providers import PROVIDERS

            # raises LLMConfigurationError if the provider's key is absent.
            self._client = LLMClient(PROVIDERS[self.provider_id])
        return self._client

    async def score(self, rubric: str, content: str) -> Judgment:
        client = self._ensure()
        prompt = f"RUBRIC:\n{rubric}\n\nRESPONSE TO SCORE:\n{content}\n\nJSON:"
        messages = [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": prompt},
        ]
        common = dict(
            temperature=0.0,
            max_tokens=400,
            purpose="eval_judge",
            model=self.model_override,
        )
        # Prefer structured-output mode (llama judges otherwise sometimes ignore
        # the reply-only-JSON instruction → scores get regex-recovered, losing
        # the explanation). But Groq's json_object is strict and 400s
        # (json_validate_failed) on some inputs — so fall back to a plain call +
        # the tolerant parser rather than dropping the whole step.
        try:
            result = await client.generate(
                messages, response_format={"type": "json_object"}, **common
            )
        except Exception as e:  # noqa: BLE001
            if "json_validate_failed" not in str(e) and "400" not in str(e):
                raise
            result = await client.generate(messages, **common)
        score, explanation = _parse(result.text)
        return Judgment(
            score=score,
            explanation=explanation,
            judge_provider=self.provider_id,
            judge_model=result.model,
        )
