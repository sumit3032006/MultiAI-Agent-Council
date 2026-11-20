"""Provider registry: OpenRouter (free models) and optional Anthropic (user's own key)."""
from __future__ import annotations

from typing import Mapping, Optional

from config import agent_key, agent_key_state
from .anthropic import AnthropicAdapter
from .base import HttpPost, ProviderAdapter, ProviderError, ProviderResult
from .openrouter import OpenRouterAdapter

ADAPTERS = {"openrouter": OpenRouterAdapter, "anthropic": AnthropicAdapter}


def build_adapter(provider: str, model: str, agent_id: str, env: Optional[Mapping[str, str]] = None,
                  http_post: Optional[HttpPost] = None) -> ProviderAdapter:
    if provider not in ADAPTERS:
        raise ProviderError("bad_model", f"unknown provider '{provider}'")
    state, msg = agent_key_state(agent_id, provider, env)
    if state != "configured":
        raise ProviderError("auth", f"API key not usable: {msg}")
    return ADAPTERS[provider](agent_key(agent_id, provider, env), model, http_post=http_post)
