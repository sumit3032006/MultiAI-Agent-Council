import asyncio
import json
import tempfile
import unittest
from pathlib import Path

import config
from providers import build_adapter
from providers.anthropic import AnthropicAdapter
from providers.base import HttpResponse, ProviderError, classify
from providers.openrouter import (OpenRouterAdapter, fetch_free_models, load_free_models, parse_free_models,
                                  pick_distinct, save_free_models)

KEY = "sk-or-" + "k" * 30


def fake_http(status, data, text="", headers=None):
    seen = {}

    async def post(url, headers_, body, timeout):
        seen.update(url=url, headers=headers_, body=body)
        return HttpResponse(status, data, text or json.dumps(data or {}), headers or {})
    post.seen = seen
    return post


def gen(adapter):
    return asyncio.run(adapter.generate("sys", "hello", 50))


class KeyAndConfigTests(unittest.TestCase):
    def test_import_all_modules(self):
        import agents.prompts, engine.council, engine.runner, database.store, dashboard.render  # noqa
        self.assertTrue(True)

    def test_key_validation(self):
        self.assertFalse(config.validate_key("openrouter", "")[0])
        self.assertFalse(config.validate_key("openrouter", "your_openrouter_key_here")[0])
        self.assertFalse(config.validate_key("openrouter", "sk-or-has space inside the key xx")[0])
        self.assertFalse(config.validate_key("openrouter", "sk-ant-" + "a" * 30)[0])
        self.assertTrue(config.validate_key("openrouter", "sk-or-v1-" + "a" * 30)[0])
        self.assertTrue(config.validate_key("anthropic", "sk-ant-" + "a" * 30)[0])
        self.assertFalse(config.validate_key("anthropic", "sk-or-" + "a" * 30)[0])

    def test_mask_never_shows_full_key(self):
        masked = config.mask_key(KEY)
        self.assertNotIn(KEY, masked)
        self.assertTrue(masked.endswith("kkkk") and len(masked) <= 8)

    def test_agent_key_own_beats_shared(self):
        env = {"OPENROUTER_API_KEY": "sk-or-shared" + "s" * 20, "OPENROUTER_API_KEY_CRITIC": "sk-or-critic" + "c" * 20}
        self.assertEqual(config.key_source("critic", "openrouter", env), "OPENROUTER_API_KEY_CRITIC")
        self.assertEqual(config.key_source("judge", "openrouter", env), "OPENROUTER_API_KEY")
        self.assertTrue(config.agent_key("critic", "openrouter", env).startswith("sk-or-critic"))
        self.assertEqual(config.agent_key_state("judge", "openrouter", {})[0], "missing")
        self.assertEqual(config.agent_key_state("judge", "openrouter", {"OPENROUTER_API_KEY": "your_key"})[0], "invalid")

    def test_provider_for_model(self):
        self.assertEqual(config.provider_for_model("claude-sonnet-5-5"), "anthropic")
        self.assertEqual(config.provider_for_model("anthropic/claude-3.5-sonnet:free"), "openrouter")
        self.assertEqual(config.provider_for_model("meta-llama/llama-3.3-70b-instruct:free"), "openrouter")

    def test_build_adapter_requires_key(self):
        with self.assertRaises(ProviderError) as cm:
            build_adapter("openrouter", "m:free", "judge", env={})
        self.assertEqual(cm.exception.kind, "auth")
        ad = build_adapter("openrouter", "m:free", "judge", env={"OPENROUTER_API_KEY_JUDGE": KEY})
        self.assertEqual(ad.api_key, KEY)

    def test_default_agents_use_five_different_free_models(self):
        agents = config.default_agents()
        models = [a.model for a in agents]
        self.assertEqual(len(set(models)), 5)
        self.assertTrue(all(m.endswith(":free") and a.provider == "openrouter" for a, m in zip(agents, models)))

    def test_write_env_values(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / ".env"
            p.write_text("# keep me\nOPENROUTER_API_KEY=old\nMAAC_PORT=7000\n", encoding="utf-8")
            config.write_env_values({"OPENROUTER_API_KEY": "new", "OPENROUTER_API_KEY_CRITIC": "c"}, p)
            text = p.read_text()
            self.assertIn("# keep me", text); self.assertIn("MAAC_PORT=7000", text)
            self.assertIn("OPENROUTER_API_KEY=new", text); self.assertNotIn("=old", text)
            self.assertIn("OPENROUTER_API_KEY_CRITIC=c", text)
            with self.assertRaises(ValueError):
                config.write_env_values({"PATH": "x"}, p)
            with self.assertRaises(ValueError):
                config.write_env_values({"OPENROUTER_API_KEY": "a b"}, p)

    def test_limits_validation_and_env(self):
        with self.assertRaises(ValueError):
            config.Limits(max_rounds=0)
        self.assertEqual(config.limits_from_env({"MAAC_MAX_ROUNDS": "6"}).max_rounds, 6)
        with self.assertRaises(ValueError):
            config.limits_from_env({"MAAC_MAX_ROUNDS": "abc"})

    def test_agents_roundtrip_and_corrupt_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "agents.json"
            config.save_agents(config.default_agents(), p)
            self.assertEqual(len(config.load_agents(p)), 5)
            p.write_text("{not json", encoding="utf-8")
            self.assertEqual([a.id for a in config.load_agents(p)], list(config.AGENT_IDS))
            bad = config.default_agents()
            bad[0] = config.AgentConfig(**{**bad[0].model_dump(), "provider": "nope"})
            with self.assertRaises(ValueError):
                config.save_agents(bad, p)


class AdapterTests(unittest.TestCase):
    def test_openrouter(self):
        http = fake_http(200, {"choices": [{"message": {"content": " hi "}}], "usage": {"prompt_tokens": 5, "completion_tokens": 7}})
        r = gen(OpenRouterAdapter(KEY, "meta/x:free", http_post=http))
        self.assertEqual((r.text, r.input_tokens, r.output_tokens), ("hi", 5, 7))
        self.assertIn("openrouter.ai", http.seen["url"])
        self.assertEqual(http.seen["headers"]["Authorization"], f"Bearer {KEY}")
        self.assertEqual(http.seen["body"]["model"], "meta/x:free")

    def test_openrouter_blocks_paid_models(self):
        http = fake_http(200, {"choices": [{"message": {"content": "x"}}]})
        with self.assertRaises(ProviderError) as cm:
            gen(OpenRouterAdapter(KEY, "openai/gpt-4o", http_post=http))
        self.assertEqual(cm.exception.kind, "bad_model")
        self.assertEqual(http.seen, {})   # no request was sent

    def test_openrouter_missing_tokens_are_none_and_empty_is_error(self):
        r = gen(OpenRouterAdapter(KEY, "m:free", http_post=fake_http(200, {"choices": [{"message": {"content": "yo"}}]})))
        self.assertEqual((r.input_tokens, r.output_tokens), (None, None))
        with self.assertRaises(ProviderError) as cm:
            gen(OpenRouterAdapter(KEY, "m:free", http_post=fake_http(200, {"choices": [{"message": {"content": None}}]})))
        self.assertEqual(cm.exception.kind, "bad_response")

    def test_openrouter_error_inside_200(self):
        http = fake_http(200, {"error": {"code": 429, "message": "Rate limit exceeded: free-models-per-day"}})
        with self.assertRaises(ProviderError) as cm:
            gen(OpenRouterAdapter(KEY, "m:free", http_post=http))
        self.assertEqual(cm.exception.kind, "quota")

    def test_anthropic(self):
        data = {"content": [{"type": "text", "text": "claude says hi"}], "usage": {"input_tokens": 3, "output_tokens": 4}}
        http = fake_http(200, data)
        r = gen(AnthropicAdapter("sk-ant-" + "a" * 30, "claude-sonnet-5-5", http_post=http))
        self.assertEqual((r.text, r.input_tokens, r.output_tokens), ("claude says hi", 3, 4))
        self.assertEqual(http.seen["url"], "https://api.anthropic.com/v1/messages")
        self.assertIn("x-api-key", http.seen["headers"])
        self.assertEqual(http.seen["body"]["system"], "sys")

    def test_anthropic_low_credit_is_quota(self):
        resp = HttpResponse(400, None, '{"error":{"message":"Your credit balance is too low to access the API"}}')
        self.assertEqual(classify(resp).kind, "quota")

    def test_error_classification(self):
        k = lambda s, t="", h=None: classify(HttpResponse(s, None, t, h or {}), KEY).kind
        self.assertEqual(k(401), "auth")
        self.assertEqual(k(402), "quota")
        self.assertEqual(k(404), "bad_model")
        self.assertEqual(k(429, "temporarily rate-limited upstream"), "rate_limit")
        self.assertEqual(k(429, "Rate limit exceeded: free-models-per-day"), "quota")
        self.assertEqual(k(503), "temporary")
        self.assertEqual(k(0, "timeout"), "temporary")
        self.assertEqual(classify(HttpResponse(429, None, "slow", {"retry-after": "7"})).retry_after, 7.0)

    def test_key_never_in_error_message(self):
        err = classify(HttpResponse(400, None, f"bad request {KEY}"), KEY)
        self.assertNotIn(KEY, str(err))


class FreeModelTests(unittest.TestCase):
    DATA = {"data": [
        {"id": "meta-llama/llama-3.3-70b-instruct:free", "name": "Llama", "context_length": 131000, "pricing": {"prompt": "0", "completion": "0"}},
        {"id": "meta-llama/llama-3.1-8b-instruct:free", "name": "Llama small", "context_length": 8000, "pricing": {"prompt": "0", "completion": "0"}},
        {"id": "google/gemma-3-27b-it:free", "name": "Gemma", "context_length": 96000, "pricing": {"prompt": "0", "completion": "0"}},
        {"id": "openai/gpt-4o", "name": "paid", "context_length": 128000, "pricing": {"prompt": "0.0000025", "completion": "0.00001"}},
        {"id": "weird/model:free", "name": "has cost", "context_length": 1, "pricing": {"prompt": "0.1", "completion": "0"}},
        {"id": "img/model:free", "name": "image only", "context_length": 5, "pricing": {"prompt": "0", "completion": "0"},
         "architecture": {"output_modalities": ["image"]}},
        {"id": "mistralai/mistral-small:free", "name": "Mistral", "context_length": 32000, "pricing": {"prompt": "0", "completion": "0"}},
    ]}

    def test_parse_keeps_only_free_text_models(self):
        ids = [m["id"] for m in parse_free_models(self.DATA)]
        self.assertEqual(ids, ["meta-llama/llama-3.3-70b-instruct:free", "google/gemma-3-27b-it:free",
                               "mistralai/mistral-small:free", "meta-llama/llama-3.1-8b-instruct:free"])

    def test_pick_distinct_prefers_different_vendors(self):
        picks = pick_distinct(parse_free_models(self.DATA), 4)
        self.assertEqual(len(set(picks)), 4)
        self.assertEqual({p.split("/")[0] for p in picks[:3]}, {"meta-llama", "google", "mistralai"})

    def test_fetch_and_cache(self):
        async def get(url, headers, timeout):
            return HttpResponse(200, self.DATA, "")
        models = asyncio.run(fetch_free_models(get))
        self.assertEqual(len(models), 4)
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "free.json"
            self.assertTrue(load_free_models(p))            # built-in fallback list
            save_free_models(models, p)
            self.assertEqual(load_free_models(p)[0]["id"], models[0]["id"])

    def test_fetch_failure_raises(self):
        async def get(url, headers, timeout):
            return HttpResponse(0, None, "timeout")
        with self.assertRaises(ProviderError):
            asyncio.run(fetch_free_models(get))


if __name__ == "__main__":
    unittest.main()
