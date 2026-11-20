"""Gradio dashboard."""
from __future__ import annotations

import asyncio
import hashlib
import os
from dataclasses import dataclass

import gradio as gr

import config
from config import AgentConfig, Limits
from database.store import Store
from engine.runner import CouncilRunner
from engine.state import CouncilState
from engine.usage import UsageManager
from providers import build_adapter
from providers import openrouter as orr
from . import render


@dataclass
class AppContext:
    store: Store
    usage: UsageManager
    state: CouncilState
    runner: CouncilRunner


def create_context(db_path=None) -> AppContext:
    store = Store(db_path or config.DB_PATH)
    usage = UsageManager(on_record=store.add_usage)
    state = CouncilState()
    runner = CouncilRunner(store, usage, state, lambda a: build_adapter(a.provider, a.model, a.id))
    return AppContext(store, usage, state, runner)


def _names(agents):
    return {a.id: a.name for a in agents}


def build_app(ctx: AppContext) -> gr.Blocks:
    env_limits = config.limits_from_env()
    verified: dict[str, str] = {}
    agents0 = config.load_agents()

    def model_choices(models=None):
        ids = [m["id"] for m in (models or orr.load_free_models(config.FREE_MODELS_PATH))]
        return ids + config.CLAUDE_MODELS

    # ---- live council ---------------------------------------------------
    def setup_items():
        items = []
        for a in config.load_agents():
            if not a.enabled:
                items.append((a.name, a.model, False, "disabled in Settings"))
                continue
            state, msg = config.agent_key_state(a.id, a.provider)
            if a.provider == "openrouter" and not a.model.endswith(":free"):
                items.append((a.name, a.model, False, "model must end in :free"))
            else:
                items.append((a.name, a.model, state == "configured", msg))
        return items

    def full():
        agents = config.load_agents()
        snap = ctx.state.snapshot()
        rows = ctx.usage.summary_rows()
        return [render.setup_html(setup_items()),
                render.status_html(snap),
                render.agent_cards_html(agents, snap, rows),
                snap["final_markdown"] or "*The final answer will appear here.*",
                snap["final_markdown"],
                render.messages_html(snap["messages"], _names(agents)),
                render.analytics_html(rows, snap, ctx.usage.task_requests, ctx.usage.task_output_tokens,
                                      ctx.usage.task_failures)]

    def _sig(vals):
        return hashlib.sha1("\x00".join(vals).encode("utf-8", "ignore")).hexdigest()

    def refresh_force():
        vals = full()
        return [*vals, _sig(vals)]

    def refresh_tick(prev):
        """Timer update: only send changes (per browser session), so the page does not flicker."""
        vals = full()
        sig = _sig(vals)
        if sig == prev:
            return [gr.update()] * len(vals) + [prev]
        return [*vals, sig]

    def start(task, rounds, requests, tokens, seconds):
        try:
            limits = Limits(max_rounds=int(rounds), max_requests=int(requests), max_output_tokens=int(tokens),
                            max_seconds=int(seconds), per_call_tokens=env_limits.per_call_tokens,
                            min_interval=env_limits.min_interval)
            ready = sum(1 for _, _, ok, _ in setup_items() if ok)
            if ready < 2:
                raise ValueError("Not ready yet: add your OpenRouter key in the Settings tab "
                                 "(Step 1), then press Auto-assign (Step 2). At least 2 agents need a working key.")
            ctx.runner.start(task, limits, config.load_agents())
        except (ValueError, RuntimeError) as e:
            out = refresh_force()
            out[1] = render.status_html({"status": "idle"}, error=False).replace(
                "Ready. Type a task and press Start Council.", render._e(e)).replace("maac-status", "maac-status err")
            return out
        return refresh_force()

    def stop():
        ctx.runner.stop()
        return refresh_force()

    # ---- history ---------------------------------------------------------
    def history_choices():
        return [(f'{t["created_at"][:16].replace("T", " ")} | {t["status"]} | {t["text"][:60]}', t["id"])
                for t in ctx.store.list_tasks()]

    def hist_refresh():
        ch = history_choices()
        return gr.update(choices=ch, value=None), gr.update(choices=ch, value=[])

    def hist_open(task_id):
        if not task_id:
            return "<div class='maac-muted'>Choose a task first.</div>", ""
        t = ctx.store.get_task(task_id)
        if not t:
            return "<div class='maac-muted'>Task not found.</div>", ""
        msgs = ctx.store.get_messages(task_id)
        final = t["final_answer"] or "*No final answer was saved for this task.*"
        return render.messages_html(msgs, _names(config.load_agents())), f"**Task:** {t['text']}\n\n{final}"

    def hist_delete(ids):
        if not ids:
            return "Select at least one task to delete.", *hist_refresh()
        if ctx.runner.is_running():
            return "Wait for the running task to finish first.", *hist_refresh()
        n = ctx.store.delete_tasks(list(ids))
        return f"Deleted {n} task(s).", *hist_refresh()

    def hist_clear(confirm):
        if not confirm:
            return "Tick the confirmation box first.", *hist_refresh()
        if ctx.runner.is_running():
            return "Wait for the running task to finish first.", *hist_refresh()
        ctx.store.clear_all()
        return "All local history deleted.", *hist_refresh()

    # ---- keys ------------------------------------------------------------
    def key_rows():
        rows = []
        for a in config.load_agents():
            state, msg = config.agent_key_state(a.id, a.provider)
            src = config.key_source(a.id, a.provider)
            masked = config.mask_key(os.environ.get(src, "")) if src else "(not set)"
            label = f"{a.provider} / {a.model}" + ("  [paid - your Anthropic account]" if a.provider == "anthropic" else "")
            rows.append((a.name, label, masked, f"{state}: {msg}", verified.get(a.id, "not tested")))
        return render.keys_html(rows)

    def apply_keys(save_to_file, shared_or, anth, *agent_keys):
        updates, problems = {}, []
        for provider, var, value in [("openrouter", "OPENROUTER_API_KEY", shared_or), ("anthropic", "ANTHROPIC_API_KEY", anth)]:
            if (value or "").strip():
                ok, why = config.validate_key(provider, value)
                (updates.__setitem__(var, value.strip()) if ok else problems.append(f"{var}: {why}"))
        for a, value in zip(config.load_agents(), agent_keys):
            if (value or "").strip():
                var = config.key_env_names(a.provider, a.id)[0]
                ok, why = config.validate_key(a.provider, value)
                (updates.__setitem__(var, value.strip()) if ok else problems.append(f"{a.name} key: {why}"))
        if problems:
            return ("❌ Nothing applied. " + "; ".join(problems), key_rows(), *[gr.update()] * (2 + len(agent_keys)))
        if not updates:
            return ("Paste a key first.", key_rows(), *[gr.update()] * (2 + len(agent_keys)))
        os.environ.update(updates)
        note = "✅ Key saved for this session."
        if save_to_file:
            try:
                config.write_env_values(updates)
                note += " Remembered in your local .env file."
            except (OSError, ValueError) as e:
                note += f" Could not save to .env: {e}"
        verified.clear()
        return (f"{note} ({len(updates)} key(s), shown masked in the table.)", key_rows(), *[""] * (2 + len(agent_keys)))

    async def _test_all():
        for a in config.load_agents():
            if not a.enabled:
                verified[a.id] = "disabled"
                continue
            try:
                ad = build_adapter(a.provider, a.model, a.id)
            except Exception as e:
                verified[a.id] = f"not available: {str(e)[:100]}"
                continue
            try:
                res = await ad.generate("Reply with the single word OK.", "Say OK.", 16, 0.0)
            except Exception as e:  # ProviderError or anything unexpected
                kind = getattr(e, "kind", "error")
                verified[a.id] = f"FAILED ({kind}): {str(e)[:110]}"
                continue
            verified[a.id] = f"WORKING ({res.latency:.1f}s)"

    def test_keys():
        if ctx.runner.is_running():
            return key_rows()
        asyncio.run(_test_all())
        return key_rows()

    # ---- free models -----------------------------------------------------
    def refresh_models():
        try:
            models = asyncio.run(orr.fetch_free_models())
        except Exception as e:
            return (f"⚠️ Could not load the model list ({str(e)[:120]}). Using the saved/built-in list.",
                    *[gr.update(choices=model_choices())] * 5)
        orr.save_free_models(models, config.FREE_MODELS_PATH)
        return (f"✅ Loaded {len(models)} free OpenRouter models into all pickers.",
                *[gr.update(choices=model_choices(models))] * 5)

    def auto_assign():
        """Pick 5 different free models and save them right away."""
        current = config.load_agents()
        picks = orr.pick_distinct(orr.load_free_models(config.FREE_MODELS_PATH), 5)
        picks += [a.model for a in current][len(picks):]
        updated = [AgentConfig(**{**a.model_dump(), "model": m, "provider": config.provider_for_model(m)})
                   for a, m in zip(current, picks[:5])]
        try:
            config.save_agents(updated)
        except ValueError as e:
            return (f"Could not save: {e}", *[gr.update()] * 5)
        verified.clear()
        return ("✅ Assigned and saved 5 different free models: " + ", ".join(picks[:5]), *picks[:5])

    def save_settings(*vals):
        agents = []
        for i, base in enumerate(config.default_agents()):
            name, role, model, en, instr = vals[i * 5:(i + 1) * 5]
            model = (model or "").strip()
            try:
                agents.append(AgentConfig(id=base.id, name=(name or "").strip(), role=(role or "").strip(),
                                          instructions=(instr or "").strip(), provider=config.provider_for_model(model),
                                          model=model, enabled=bool(en)))
            except ValueError as e:
                return f"❌ Could not save ({base.id}): {str(e)[:200]}"
        try:
            config.save_agents(agents)
        except ValueError as e:
            return f"❌ Could not save: {e}"
        verified.clear()
        return "✅ Saved. New settings apply to the next task."

    def reset_availability():
        ctx.usage.reset()
        return "✅ Availability reset. Exhausted keys will be tried again."

    # ---- layout ----------------------------------------------------------
    choices = model_choices()
    examples = [["Compare SQLite, PostgreSQL and MongoDB for a small student-project web app and recommend one."],
                ["Plan a 4-week study roadmap to become a junior backend developer using Python."],
                ["Design a simple offline-first note-taking app: features, tech stack and risks."]]
    with gr.Blocks(title="Multi-AI Agent Council", css=render.CSS,
                   theme=gr.themes.Base(primary_hue="indigo", neutral_hue="slate")) as demo:
        sig = gr.State("")
        gr.HTML(render.header_html())
        with gr.Tabs() as tabs:
            with gr.Tab("🏛️ Council", id="council"):
                setup = gr.HTML()
                task = gr.Textbox(label="What should the council work on?", lines=4, max_lines=12,
                                  placeholder="Describe your task in plain words (10-4000 characters)...")
                gr.Examples(examples=examples, inputs=task, label="Try an example")
                with gr.Row():
                    start_btn = gr.Button("▶ Start Council", variant="primary", scale=4, size="lg")
                    stop_btn = gr.Button("■ Stop", variant="stop", scale=1, size="lg")
                status = gr.HTML()
                gr.Markdown("### 🧑‍🤝‍🧑 The council")
                cards = gr.HTML()
                gr.Markdown("### 📋 Final answer")
                with gr.Group(elem_classes="maac-final"):
                    final = gr.Markdown("*The final answer will appear here.*")
                with gr.Accordion("Copy final answer as plain text", open=False):
                    plain = gr.Textbox(label="", lines=8, show_copy_button=True, interactive=False)
                with gr.Accordion("⚙️ Safety limits (optional)", open=False):
                    with gr.Row():
                        rounds = gr.Number(env_limits.max_rounds, label="Max discussion rounds", precision=0)
                        requests = gr.Number(env_limits.max_requests, label="Max API requests", precision=0)
                        tokens = gr.Number(env_limits.max_output_tokens, label="Max output tokens", precision=0)
                        seconds = gr.Number(env_limits.max_seconds, label="Max seconds", precision=0)
            with gr.Tab("💬 Discussion", id="discussion"):
                feed = gr.HTML()
            with gr.Tab("📊 Analytics", id="analytics"):
                analytics = gr.HTML()
                reset_btn = gr.Button("Reset availability (use after quotas renew)")
                reset_msg = gr.Markdown()
            with gr.Tab("🗂️ History", id="history"):
                with gr.Row():
                    pick = gr.Dropdown(label="Previous tasks", choices=[], interactive=True, scale=4)
                    hist_ref = gr.Button("Refresh list", scale=1)
                open_btn = gr.Button("Open selected task", variant="primary")
                h_final = gr.Markdown()
                h_feed = gr.HTML()
                with gr.Accordion("Delete history", open=False):
                    multi = gr.Dropdown(label="Select tasks to delete", choices=[], multiselect=True)
                    del_btn = gr.Button("Delete selected tasks")
                    with gr.Row():
                        confirm = gr.Checkbox(label="I understand this deletes ALL local history")
                        clear_btn = gr.Button("Clear all history", variant="stop")
                h_msg = gr.Markdown()
            with gr.Tab("⚙️ Settings", id="settings"):
                gr.Markdown("## Step 1 - Add your OpenRouter key\nPaste one key (used by all agents), then press **Save key**. "
                            "Get one free at openrouter.ai/keys. Keys are masked after saving and never shown in full.")
                with gr.Row():
                    shared_or = gr.Textbox(label="OpenRouter API key (shared by all agents)", type="password", scale=4,
                                           placeholder="sk-or-v1-...")
                    apply_btn = gr.Button("Save key", variant="primary", scale=1)
                save_env = gr.Checkbox(label="Remember on this computer (saves to the local .env file)", value=True)
                with gr.Accordion("Optional: a different key per agent, or a Claude key", open=False):
                    gr.Markdown("A key typed here wins over the shared key for that agent. The **Anthropic (Claude)** key is "
                                "**paid** and billed to your Anthropic account; it is only used if you give an agent a `claude-...` model.")
                    anth = gr.Textbox(label="Anthropic (Claude) key - optional", type="password", placeholder="sk-ant-...")
                    key_boxes = [gr.Textbox(label=f"{a.name}: own key (OpenRouter or Anthropic)", type="password")
                                 for a in agents0]
                k_msg = gr.Markdown()
                keys = gr.HTML()
                gr.Markdown("## Step 2 - Pick the models\nPress **Auto-assign** to give the five agents five different free models "
                            "(saved immediately). You can also choose any model per agent below.")
                with gr.Row():
                    auto_btn = gr.Button("✨ Auto-assign 5 different free models", variant="primary")
                    models_btn = gr.Button("↻ Refresh free model list")
                m_msg = gr.Markdown()
                comps, model_dds = [], []
                with gr.Accordion("Advanced: edit agents, roles and models", open=False):
                    for a in agents0:
                        with gr.Accordion(f"{a.name}", open=False):
                            with gr.Row():
                                c_name = gr.Textbox(a.name, label="Name")
                                c_role = gr.Textbox(a.role, label="Role title")
                                c_model = gr.Dropdown(choices, value=a.model, label="Model", allow_custom_value=True)
                                c_en = gr.Checkbox(a.enabled, label="Enabled")
                            c_ins = gr.Textbox(a.instructions, label="Role instructions", lines=3)
                        comps += [c_name, c_role, c_model, c_en, c_ins]
                        model_dds.append(c_model)
                    save_btn = gr.Button("Save agent settings", variant="primary")
                    s_msg = gr.Markdown()
                gr.Markdown("## Step 3 - Test\nSends one tiny request per enabled agent to check the key and model.")
                test_btn = gr.Button("🔌 Test connections")

        live = [setup, status, cards, final, plain, feed, analytics]
        start_btn.click(start, [task, rounds, requests, tokens, seconds], [*live, sig])
        stop_btn.click(stop, None, [*live, sig])
        gr.Timer(1.5).tick(refresh_tick, sig, [*live, sig])
        demo.load(refresh_force, None, [*live, sig])
        demo.load(hist_refresh, None, [pick, multi])
        demo.load(key_rows, None, keys)
        reset_btn.click(reset_availability, None, reset_msg)
        hist_ref.click(hist_refresh, None, [pick, multi])
        open_btn.click(hist_open, pick, [h_feed, h_final])
        del_btn.click(hist_delete, multi, [h_msg, pick, multi])
        clear_btn.click(hist_clear, confirm, [h_msg, pick, multi])
        apply_btn.click(apply_keys, [save_env, shared_or, anth, *key_boxes], [k_msg, keys, shared_or, anth, *key_boxes])
        test_btn.click(test_keys, None, keys)
        models_btn.click(refresh_models, None, [m_msg, *model_dds])
        auto_btn.click(auto_assign, None, [m_msg, *model_dds])
        save_btn.click(save_settings, comps, s_msg)
    return demo
