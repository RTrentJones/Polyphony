"""Provider-fungible LLM backend.

One OpenAI-compatible adapter (parameterized by base_url) covers Gemini, Groq,
xAI, and OpenAI; the active provider is chosen by LLM_PROVIDER and gated on its
own API-key env var. See providers.py for the registry and client.py for the
paced, accounted generation entry point.
"""

from .providers import PROVIDERS, Provider, GenResult, active_provider
from .client import LLMClient, get_llm_client

__all__ = [
    "PROVIDERS",
    "Provider",
    "GenResult",
    "active_provider",
    "LLMClient",
    "get_llm_client",
]
