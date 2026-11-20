from __future__ import annotations

from .base import ProviderAdapter


class AnthropicAdapter(ProviderAdapter):
    """Claude via the Anthropic Messages API. Only used if the user enters their own key and assigns a Claude model.
    This is NOT free: usage is billed to the user's Anthropic account."""

    name = "anthropic"

    def build_request(self, system, user, max_tokens, temperature):
        headers = {"x-api-key": self.api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
        body = {"model": self.model, "max_tokens": max_tokens, "temperature": min(temperature, 1.0), "system": system,
                "messages": [{"role": "user", "content": user}]}
        return "https://api.anthropic.com/v1/messages", headers, body

    def parse_response(self, data):
        text = "".join(b.get("text", "") for b in data["content"] if b.get("type") == "text")
        u = data.get("usage") or {}
        return text, u.get("input_tokens"), u.get("output_tokens")
