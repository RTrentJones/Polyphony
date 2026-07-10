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

    def _ensure(self):
        if self._client is None:
            from app.llm.client import LLMClient
            from app.llm.providers import PROVIDERS

            provider = PROVIDERS.get(self._cfg.judge_provider)
            if provider is None:
                raise RuntimeError(
                    f"unknown judge provider {self._cfg.judge_provider!r}"
                )
            # raises LLMConfigurationError if the provider's key is absent.
            self._client = LLMClient(provider)
        return self._client

    async def score(self, rubric: str, content: str) -> Judgment:
        client = self._ensure()
        prompt = f"RUBRIC:\n{rubric}\n\nRESPONSE TO SCORE:\n{content}\n\nJSON:"
        result = await client.generate(
            [
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": prompt},
            ],
            temperature=0.0,
            max_tokens=400,
            purpose="eval_judge",
            model=self._cfg.judge_model,
        )
        score, explanation = _parse(result.text)
        return Judgment(
            score=score,
            explanation=explanation,
            judge_provider=self._cfg.judge_provider,
            judge_model=result.model,
        )
