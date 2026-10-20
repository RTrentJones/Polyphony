"""USD per 1M tokens by resolved model id; no row = unpriced (SPEC-81)."""

from typing import NamedTuple, Optional


class Price(NamedTuple):
    in_per_m: float
    out_per_m: float
    source: str
    date: str

    def cost(self, tokens_in: int, tokens_out: int) -> float:
        return (tokens_in * self.in_per_m + tokens_out * self.out_per_m) / 1e6


_FREE_MODELS = (  # free tiers and the stub; Gemini only on an unbilled project
    "openai/gpt-oss-120b openai/gpt-oss-20b llama-3.3-70b llama3.1-8b"
    " google/gemma-4-31b-it:free mistral-small-latest"
    " google/gemma-4-26b-a4b-it:free stub-default stub-fast"
).split()
# No row carries a date on which nobody read its source (W12-9 re-prices).
_CARRIED = "carried from Provider.rate_in @ e954eae; not re-verified"
_FREE = Price(0, 0, "free tier or local stub, per the provider registry", "unverified")
_READ = "gemini-3.6-flash gemini-3.5-flash-lite nvidia/nemotron-3-ultra-550b-a55b:free"
_FREE_READ = Price(0, 0, "https://ai.google.dev/gemini-api/docs/rate-limits; https://openrouter.ai/api/v1/key", "2026-10-20")  # fmt: skip
PRICES: dict[str, Price] = {"gpt-4o": Price(2.5, 10.0, _CARRIED, "unverified")}
PRICES |= dict.fromkeys(_FREE_MODELS, _FREE) | dict.fromkeys(_READ.split(), _FREE_READ)


def cost_usd(model: str, tokens_in: int, tokens_out: int) -> Optional[float]:
    price = PRICES.get(model)
    return None if price is None else price.cost(tokens_in, tokens_out)
