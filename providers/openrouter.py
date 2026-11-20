"""OpenRouter adapter + helpers for the list of free models."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Awaitable, Callable, Optional

from .base import HttpResponse, OpenAICompatAdapter, ProviderError, httpx_get

MODELS_URL = "https://openrouter.ai/api/v1/models"

# Used only until the live list has been loaded. Free models come and go; refresh in Settings.
FALLBACK_FREE_MODELS = [
    "meta-llama/llama-3.3-70b-instruct:free", "openai/gpt-oss-20b:free", "google/gemma-3-27b-it:free",
    "mistralai/mistral-small-3.2-24b-instruct:free", "z-ai/glm-4.5-air:free", "qwen/qwen3-coder:free",
    "moonshotai/kimi-k2:free", "deepseek/deepseek-r1-0528:free",
]


class OpenRouterAdapter(OpenAICompatAdapter):
    """OpenRouter API. Only model ids ending in ':free' are allowed (zero-budget guard)."""

    name = "openrouter"
    url = "https://openrouter.ai/api/v1/chat/completions"

    def check_model(self) -> None:
        if not self.model.endswith(":free"):
            raise ProviderError("bad_model", "OpenRouter models must end with ':free' (paid models are blocked by this app)")

    def extra_headers(self) -> dict:
        return {"X-Title": "Multi-AI Agent Council"}


def parse_free_models(data: dict) -> list[dict]:
    """Extract free text models from OpenRouter's /models response: [{'id','name','context'}]."""
    out = []
    for m in data.get("data", []):
        mid = str(m.get("id", ""))
        if not mid.endswith(":free"):
            continue
        pricing = m.get("pricing") or {}
        try:
            if float(pricing.get("prompt", 0) or 0) != 0 or float(pricing.get("completion", 0) or 0) != 0:
                continue
        except (TypeError, ValueError):
            continue
        outputs = (m.get("architecture") or {}).get("output_modalities")
        if outputs and "text" not in outputs:
            continue
        out.append({"id": mid, "name": str(m.get("name", mid)), "context": int(m.get("context_length") or 0)})
    return sorted(out, key=lambda x: (-x["context"], x["id"]))


def pick_distinct(models: list[dict], n: int = 5) -> list[str]:
    """Choose n different models, preferring different vendors (largest context first)."""
    chosen, vendors = [], set()
    for m in models:
        vendor = m["id"].split("/")[0]
        if vendor not in vendors:
            chosen.append(m["id"]); vendors.add(vendor)
        if len(chosen) == n:
            return chosen
    for m in models:
        if m["id"] not in chosen:
            chosen.append(m["id"])
        if len(chosen) == n:
            break
    return chosen


async def fetch_free_models(http_get: Optional[Callable[..., Awaitable[HttpResponse]]] = None) -> list[dict]:
    """Download the current model list (public endpoint, no key needed). Raises ProviderError on failure."""
    resp = await (http_get or httpx_get)(MODELS_URL, {}, 30.0)
    if resp.status != 200 or not isinstance(resp.data, dict):
        raise ProviderError("temporary", f"could not load the OpenRouter model list (HTTP {resp.status})")
    models = parse_free_models(resp.data)
    if not models:
        raise ProviderError("bad_response", "OpenRouter returned no free models")
    return models


def save_free_models(models: list[dict], path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(models), encoding="utf-8")


def load_free_models(path: Path) -> list[dict]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if isinstance(data, list) and data and all("id" in m for m in data):
            return data
    except (OSError, ValueError, TypeError):
        pass
    return [{"id": m, "name": m, "context": 0} for m in FALLBACK_FREE_MODELS]
