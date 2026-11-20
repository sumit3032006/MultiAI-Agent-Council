# Build Report - Multi-AI Agent Council v1.1 (OpenRouter edition)

## What changed from v1.0
Gemini, Groq, Cloudflare and Cohere adapters were removed. The app now uses OpenRouter only (one key per agent or one shared key,
five different free models) plus an optional Anthropic adapter for a user-entered Claude key.

## Implemented
* Five configurable agents (name, role, instructions, model, enabled). Provider is derived from the model: `claude-...` -> Anthropic, otherwise OpenRouter.
* OpenRouter adapter refuses any model not ending in `:free`. Optional Anthropic adapter is used only when the user enters a key and assigns a Claude model;
  it is **not free** (billed to the user's Anthropic account) and is labelled that way in the UI. Nothing is bought or upgraded automatically.
* Dashboard key entry (password fields, per-agent or shared, session-only or saved to local `.env`), masked key table, live "Test connections".
* "Refresh free models" loads the current free text models from OpenRouter's public model list (cached in `data/`), fills all model pickers;
  "Auto-assign 5 different free models" picks different vendors. Custom model ids can be typed.
* Quota state is tracked **per API key** (non-secret label such as "openrouter key #a1b2"): agents with different keys are independent, agents sharing a key share its state.
* Hybrid communication, adaptive Judge-controlled rounds, limits (rounds/requests/reported tokens/seconds/Stop), Judge fallback (PROVISIONAL / NOT FULLY REVIEWED),
  duplicate filtering, usage manager, SQLite history (open/delete/clear), dark Gradio dashboard, START.bat, SETUP_GUIDE.md.

## Structure
`app.py, config.py, agents/prompts.py, providers/{base,openrouter,anthropic}.py, engine/{council,gateway,usage,state,messages,runner}.py,
database/store.py, dashboard/{render,ui}.py, tests/`

## Tests executed (build sandbox)
55 automated tests: 54 passed, 1 skipped (dashboard startup: Gradio not installable offline). Covered: imports, OpenRouter and Anthropic adapters with mock
responses, free-model parsing/selection/caching, key validation, own-vs-shared key lookup, .env writing (comments kept, only key names allowed),
error classification, single retry, quota exhaustion, shared-key quota, cooldown, request cap, routing, agent-to-agent visibility, adaptive rounds,
round/request/token/time limits, manual stop, unavailable agents, Judge fallback, no-evaluator result, duplicate suppression, SQLite persistence, background runner.

**Caveat:** the sandbox had no internet, so pydantic, httpx, pytest and gradio were not installed. Tests ran under `unittest` with a throw-away pydantic stand-in
(not in the ZIP). Run `python -m pytest` after installing requirements to confirm on a real setup.

## NOT verified
* **No real API request was made; no provider is verified.** OpenRouter/Anthropic request formats follow their public docs from memory; default free-model ids
  are placeholders that may no longer be free - use "Refresh free models".
* The Gradio dashboard (including the key and model controls) was only syntax-checked, never launched. START.bat was not run on Windows.
* Quota-vs-rate-limit detection is a heuristic on error text. Whether several keys from one OpenRouter account share limits is my understanding, not tested.
* Claude model ids (`claude-sonnet-5-5`, `claude-opus-5-5`, `claude-haiku-5-5`) are the names I was given; type another id if needed.

## Known limitations
No LangGraph (plain asyncio loop). Token limit counts provider-reported tokens only. One task at a time. Quota state is in memory.

## First start
Extract, run START.bat, open Settings, enter keys (Apply keys), Refresh free models, Auto-assign, Save, Test connections, then run a task.
