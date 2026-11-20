"""Common provider interface, error classification and HTTP helper."""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional


class ProviderError(Exception):
    """kind: quota | rate_limit | auth | bad_model | temporary | bad_request | bad_response"""

    def __init__(self, kind: str, message: str, retry_after: Optional[float] = None, status: Optional[int] = None):
        super().__init__(message)
        self.kind = kind
        self.retry_after = retry_after
        self.status = status


@dataclass
class HttpResponse:
    status: int
    data: Optional[dict]
    text: str = ""
    headers: dict = field(default_factory=dict)


@dataclass
class ProviderResult:
    text: str
    input_tokens: Optional[int]   # None = provider did not report it
    output_tokens: Optional[int]
    latency: float


HttpPost = Callable[[str, dict, dict, float], Awaitable[HttpResponse]]


async def httpx_post(url: str, headers: dict, body: dict, timeout: float = 60.0) -> HttpResponse:
    import httpx  # imported lazily so tests run without network libraries

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(url, headers=headers, json=body)
    except httpx.TimeoutException:
        return HttpResponse(0, None, "timeout")
    except httpx.HTTPError as e:
        return HttpResponse(0, None, type(e).__name__)
    try:
        data = r.json()
    except ValueError:
        data = None
    return HttpResponse(r.status_code, data if isinstance(data, dict) else None, r.text[:2000],
                        {k.lower(): v for k, v in r.headers.items()})


async def httpx_get(url: str, headers: dict, timeout: float = 30.0) -> HttpResponse:
    import httpx

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.get(url, headers=headers)
    except httpx.TimeoutException:
        return HttpResponse(0, None, "timeout")
    except httpx.HTTPError as e:
        return HttpResponse(0, None, type(e).__name__)
    try:
        data = r.json()
    except ValueError:
        data = None
    return HttpResponse(r.status_code, data if isinstance(data, dict) else None, r.text[:500])


# Words that indicate a daily/monthly/credit quota (as opposed to a short per-minute rate limit).
_QUOTA_WORDS = ("perday", "per day", "per-day", "daily", "month", "trial key", "used up",
                "insufficient", "free allocation", "credit balance")


def redact(text: str, key: str = "") -> str:
    text = text or ""
    if key and len(key) >= 8:
        text = text.replace(key, "[key]")
    return re.sub(r"\s+", " ", text).strip()


def classify(resp: HttpResponse, key: str = "") -> ProviderError:
    s = resp.status
    low = (resp.text or "").lower()
    snippet = redact(resp.text, key)[:200]
    if s == 0:
        return ProviderError("temporary", f"network error or timeout ({snippet})", status=0)
    if s in (401, 403) or "api key not valid" in low or "api_key_invalid" in low:
        return ProviderError("auth", f"API key rejected (HTTP {s}). Check the key in .env", status=s)
    if s == 402:
        return ProviderError("quota", "payment/credits required - free quota is used up", status=s)
    if "credit balance" in low:
        return ProviderError("quota", "account credit balance is too low", status=s)
    if s == 404:
        return ProviderError("bad_model", f"model or endpoint not found (HTTP 404): {snippet}", status=s)
    if s == 429:
        if any(w in low for w in _QUOTA_WORDS):
            return ProviderError("quota", f"free quota exhausted: {snippet}", status=s)
        ra = None
        try:
            ra = float(resp.headers.get("retry-after"))
        except (TypeError, ValueError):
            pass
        return ProviderError("rate_limit", f"rate limit reached: {snippet}", retry_after=ra, status=s)
    if s in (408, 500, 502, 503, 504, 529):
        return ProviderError("temporary", f"provider temporarily unavailable (HTTP {s})", status=s)
    return ProviderError("bad_request", f"HTTP {s}: {snippet}", status=s)


class ProviderAdapter:
    """Subclasses implement build_request() and parse_response()."""

    name = "base"

    def __init__(self, api_key: str, model: str, http_post: Optional[HttpPost] = None, timeout: float = 60.0):
        self.api_key = api_key
        self.model = model
        self.http_post = http_post or httpx_post
        self.timeout = timeout

    def check_model(self) -> None:
        """Raise ProviderError('bad_model') if the model is not allowed (zero-budget guard)."""

    def build_request(self, system: str, user: str, max_tokens: int, temperature: float):
        raise NotImplementedError

    def parse_response(self, data: dict):
        """Return (text, input_tokens | None, output_tokens | None)."""
        raise NotImplementedError

    async def generate(self, system: str, user: str, max_tokens: int = 800, temperature: float = 0.6) -> ProviderResult:
        self.check_model()
        url, headers, body = self.build_request(system, user, max_tokens, temperature)
        t0 = time.monotonic()
        resp = await self.http_post(url, headers, body, self.timeout)
        latency = time.monotonic() - t0
        if resp.status != 200:
            raise classify(resp, self.api_key)
        if not isinstance(resp.data, dict):
            raise ProviderError("bad_response", "provider returned something that is not JSON")
        try:
            text, tin, tout = self.parse_response(resp.data)
        except ProviderError:
            raise
        except (KeyError, IndexError, TypeError, ValueError):
            raise ProviderError("bad_response", "unexpected response format (blocked or empty answer?)") from None
        if not (text or "").strip():
            raise ProviderError("bad_response", "provider returned an empty answer")
        return ProviderResult(text.strip(), tin, tout, latency)


class OpenAICompatAdapter(ProviderAdapter):
    """Shared by providers that expose the OpenAI chat-completions format (Groq, OpenRouter)."""

    url = ""

    def extra_headers(self) -> dict:
        return {}

    def build_request(self, system, user, max_tokens, temperature):
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json", **self.extra_headers()}
        body = {"model": self.model, "max_tokens": max_tokens, "temperature": temperature,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        return self.url, headers, body

    def parse_response(self, data):
        if "choices" not in data and isinstance(data.get("error"), dict):
            code = data["error"].get("code")
            status = code if isinstance(code, int) else 502
            raise classify(HttpResponse(status, data, str(data["error"].get("message", ""))), self.api_key)
        text = data["choices"][0]["message"].get("content") or ""
        usage = data.get("usage") or {}
        return text, usage.get("prompt_tokens"), usage.get("completion_tokens")
