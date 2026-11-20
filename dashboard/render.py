"""HTML renderers for the dashboard (no Gradio import, so they are unit-testable)."""
from __future__ import annotations

import html

CSS = """
:root,.dark,.gradio-container{
 --body-background-fill:#0b0e1a;--background-fill-primary:#141833;--background-fill-secondary:#10142a;
 --block-background-fill:#141833;--body-text-color:#e8eaf6;--body-text-color-subdued:#9aa3c7;
 --block-label-text-color:#a9b1d6;--block-title-text-color:#e8eaf6;--border-color-primary:#2a3050;
 --input-background-fill:#0e1226;--input-border-color:#2f3660;--button-secondary-background-fill:#222850;
 --button-secondary-text-color:#e8eaf6;--color-accent:#6d7bff;--table-even-background-fill:#141833;--table-odd-background-fill:#10142a}
.gradio-container{background:radial-gradient(1200px 500px at 10% -10%,#1b2150 0,#0b0e1a 60%)!important;color:#e8eaf6!important;
 font-family:Segoe UI,Inter,system-ui,Arial,sans-serif!important;max-width:1180px!important;margin:auto!important}
button.primary{background:linear-gradient(135deg,#5b6cff,#8b5cff)!important;border:none!important;font-weight:700!important;font-size:16px!important}
button.stop{font-weight:700!important}
.maac-hero{padding:18px 22px;border-radius:16px;background:linear-gradient(135deg,#1c2260,#2a1a5e);border:1px solid #3a4190;margin-bottom:6px}
.maac-hero h1{margin:0;font-size:28px;color:#fff}.maac-hero p{margin:6px 0 0;color:#c4caf0;font-size:15px}
.maac-muted{color:#9aa3c7;font-size:12px}
.maac-setup{display:flex;flex-wrap:wrap;gap:8px;align-items:center;padding:12px 14px;border-radius:12px;background:#141833;border:1px solid #2a3050;font-size:14px}
.maac-setup.ok{border-color:#1f7a55}.maac-setup.warn{border-color:#a8742a;background:#251d12}
.maac-pill{padding:3px 10px;border-radius:999px;font-size:12px;font-weight:600}
.p-ok{background:#16402f;color:#7df0b5}.p-bad{background:#4a2020;color:#ff9aa8}
.maac-steps{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px}
.maac-step{padding:4px 12px;border-radius:999px;font-size:12px;background:#1b2040;color:#7f89b5;border:1px solid #2a3050}
.maac-step.done{background:#16402f;color:#7df0b5;border-color:#1f7a55}
.maac-step.active{background:#3b3a14;color:#ffe27a;border-color:#a8902a;animation:maacpulse 1.2s infinite}
@keyframes maacpulse{50%{opacity:.55}}
.maac-status{padding:12px 16px;border-radius:12px;background:#141833;border:1px solid #2a3050;font-size:15px}
.maac-status.err{border-color:#ff6b81}
.maac-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px}
.maac-card{background:#141833;border:1px solid #2a3050;border-radius:14px;padding:14px;font-size:13px;line-height:1.45}
.maac-card h4{margin:6px 0 0;font-size:16px;color:#fff}.maac-ico{font-size:26px}
.maac-badge{display:inline-block;padding:2px 9px;border-radius:999px;font-size:11px;font-weight:700;margin:6px 0}
.b-IDLE,.b-DONE{background:#16402f;color:#7df0b5}.b-WORKING{background:#4d3f12;color:#ffd866;animation:maacpulse 1.2s infinite}
.b-QUOTA{background:#5a2a12;color:#ffb27a}.b-OFFLINE,.b-FAILED{background:#5a1a24;color:#ff8c9b}.b-DISABLED{background:#2a2f45;color:#9aa3c7}
.maac-chat{display:flex;gap:10px;margin:10px 0;align-items:flex-start}
.maac-av{flex:0 0 38px;height:38px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:19px;background:#1b2040;border:2px solid var(--c,#5b6cff)}
.maac-bub{flex:1;background:#141833;border:1px solid #2a3050;border-left:4px solid var(--c,#5b6cff);border-radius:12px;padding:10px 14px;font-size:14px;line-height:1.5}
.maac-meta{color:#9aa3c7;font-size:12px;margin-bottom:4px}
.maac-type{display:inline-block;padding:1px 8px;border-radius:6px;font-size:11px;font-weight:700;background:#222850;color:#cfd5ff;margin-left:6px}
.t-CHALLENGE .maac-type{background:#5a1a24;color:#ff9aa8}.t-QUESTION .maac-type{background:#4d3f12;color:#ffd866}
.t-DEFENCE .maac-type{background:#16402f;color:#7df0b5}.t-REVISION .maac-type{background:#3a2a66;color:#c9a8ff}
.t-EVALUATION .maac-type,.t-FINAL .maac-type{background:#10405a;color:#7fe0ff}.t-VOTE .maac-type{background:#5a3a12;color:#ffbf70}
.t-SYSTEM .maac-bub{opacity:.8;border-style:dashed}
.maac-round{margin:18px 0 4px;color:#aab3ea;font-weight:700;font-size:13px;letter-spacing:.06em;text-transform:uppercase;border-bottom:1px solid #2a3050;padding-bottom:4px}
.maac-tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin-bottom:12px}
.maac-tile{background:#141833;border:1px solid #2a3050;border-radius:12px;padding:12px}
.maac-tile b{display:block;font-size:24px;color:#fff}.maac-tile span{color:#9aa3c7;font-size:12px}
.maac-table{width:100%;border-collapse:collapse;font-size:13px}.maac-table th,.maac-table td{border-bottom:1px solid #2a3050;padding:7px 9px;text-align:left}
.maac-final{background:#141833;border:1px solid #3a4190;border-radius:14px;padding:6px 18px}
"""

NAMES = {"all": "Everyone", "system": "System", "user": "You"}
STYLE = {"researcher": ("🔎", "#4dd2ff"), "technical": ("🛠️", "#7df0b5"), "critic": ("🧐", "#ff8c9b"),
         "innovator": ("💡", "#ffd866"), "judge": ("⚖️", "#b18cff"), "system": ("⚙️", "#7a82a6")}


def _e(s) -> str:
    return html.escape(str(s if s is not None else ""))


def _style(agent_id: str):
    return STYLE.get(agent_id, ("🤖", "#5b6cff"))


def _badge(status: str) -> str:
    key = status.split()[0] if status else "IDLE"
    return f'<span class="maac-badge b-{_e(key)}">{_e(status)}</span>'


def header_html() -> str:
    return ('<div class="maac-hero"><h1>🏛️ Multi-AI Agent Council</h1>'
            '<p>Five AI agents discuss your task, challenge each other and agree on one answer. '
            'Type a task, press <b>Start Council</b>.</p></div>')


def setup_html(items: list[tuple[str, str, bool, str]]) -> str:
    """items: (agent name, model, ready, message). Shows whether the app is ready to run."""
    ready = sum(1 for _, _, ok, _ in items if ok)
    pills = "".join(
        f'<span class="maac-pill {"p-ok" if ok else "p-bad"}" title="{_e(msg)}">{"✓" if ok else "✗"} {_e(name)}</span>'
        for name, _, ok, msg in items)
    if ready >= 2:
        head = f"<b>✅ Ready</b> - {ready} of {len(items)} agents can run."
        cls = "ok"
    else:
        head = ("<b>⚠️ Setup needed</b> - add your OpenRouter key in the <b>Settings</b> tab "
                f"(only {ready} of {len(items)} agents ready; at least 2 are needed).")
        cls = "warn"
    return f'<div class="maac-setup {cls}"><div>{head}</div><div>{pills}</div></div>'


def _stepper(snap: dict) -> str:
    st, phase, rnd = snap.get("status"), (snap.get("phase") or "").lower(), snap.get("round", 0)
    if st not in ("running", "stopping", "finished"):
        return ""
    steps = ["Proposals", "Debate", "Judge review", "Voting", "Final answer"]
    if st == "finished":
        cur = len(steps)
    elif "final" in phase:
        cur = 4
    elif "voting" in phase:
        cur = 3
    elif "evaluating" in phase:
        cur = 2
    else:
        cur = 1 if rnd >= 2 else 0
    chips = "".join(
        f'<span class="maac-step {"done" if i < cur else "active" if i == cur else ""}">{"✓ " if i < cur else ""}{s}</span>'
        for i, s in enumerate(steps))
    return f'<div class="maac-steps">{chips}</div>'


def status_html(snap: dict, error: bool = False) -> str:
    st = snap.get("status", "idle")
    labels = {"idle": "Ready. Type a task and press Start Council.", "running": "⏳ Running", "stopping": "Stopping...",
              "finished": "✅ Finished", "stopped": "Stopped by user", "failed": "❌ Failed"}
    text = labels.get(st, st)
    if st in ("running", "stopping"):
        text += f" - round {snap.get('round', 0)} - {snap.get('phase', '')}"
    elif snap.get("stop_reason") and st == "finished":
        text += f" - {snap['stop_reason']}"
    if snap.get("error"):
        text += f" - {snap['error']}"
    if snap.get("provisional"):
        text += " - PROVISIONAL result (Judge unavailable)"
    cls = "maac-status err" if (error or st == "failed") else "maac-status"
    return f'<div class="{cls}">{_e(text)}{_stepper(snap)}</div>'


def agent_cards_html(agents: list, snap: dict, usage_rows: list[dict]) -> str:
    live = snap.get("agents", {})
    cards = []
    for a in agents:
        s = live.get(a.id, {})
        status = s.get("status") or ("DISABLED" if not a.enabled else "IDLE")
        lat = s.get("last_latency")
        icon, color = _style(a.id)
        cards.append(
            f'<div class="maac-card" style="border-top:3px solid {color}"><div class="maac-ico">{icon}</div>'
            f'<h4>{_e(a.name)}</h4><div class="maac-muted">{_e(a.role)}</div>{_badge(status)}'
            f'<div>{_e(a.provider)}</div><div class="maac-muted">{_e(a.model)}</div>'
            f'<div class="maac-muted">Doing: {_e(s.get("activity", "Waiting"))}</div>'
            f'<div class="maac-muted">Last response: {_e(f"{lat}s" if lat is not None else "-")}</div>'
            f'<div class="maac-muted">API: {_e(s.get("api", "not checked yet"))}</div></div>')
    return '<div class="maac-grid">' + "".join(cards) + "</div>"


def messages_html(messages: list[dict], names: dict[str, str]) -> str:
    if not messages:
        return '<div class="maac-muted" style="padding:18px">No messages yet. Start the council to watch the agents talk.</div>'

    def n(i):
        return NAMES.get(i) or names.get(i, i)

    out, last_round = [], None
    for m in messages:
        if m["round"] != last_round:
            last_round = m["round"]
            out.append(f'<div class="maac-round">Round {_e(last_round)}</div>')
        icon, color = _style(m["sender"])
        body = _e(m["content"]).replace("\n", "<br>")
        out.append(
            f'<div class="maac-chat t-{_e(m["type"])}"><div class="maac-av" style="--c:{color}">{icon}</div>'
            f'<div class="maac-bub" style="--c:{color}"><div class="maac-meta"><b>{_e(n(m["sender"]))}</b> &rarr; '
            f'<b>{_e(n(m["receiver"]))}</b><span class="maac-type">{_e(m["type"])}</span> &middot; round {_e(m["round"])}</div>'
            f'{body}</div></div>')
    return "".join(out)


def analytics_html(rows: list[dict], snap: dict, task_requests: int, task_out: int, task_fail: int) -> str:
    agents = snap.get("agents", {})
    active = [k for k, v in agents.items() if v.get("status") in ("IDLE", "WORKING")]
    skipped = [k for k, v in agents.items() if v.get("status") not in ("IDLE", "WORKING", None)]
    tiles = "".join(f'<div class="maac-tile"><b>{_e(v)}</b><span>{_e(label)}</span></div>' for label, v in [
        ("Requests (this task)", task_requests), ("Output tokens (reported)", task_out),
        ("Failed requests", task_fail), ("Active agents", len(active)), ("Skipped agents", len(skipped))])
    head = ("<tr><th>API key</th><th>Model</th><th>Requests</th><th>Failed</th><th>Rate-limit</th><th>Input tok.</th>"
            "<th>Output tok.</th><th>Avg time</th><th>Availability</th><th>Cost</th></tr>")
    body = "".join(
        f'<tr><td>{_e(r["provider"])}</td><td>{_e(r["model"])}</td><td>{r["requests"]}</td><td>{r["failures"]}</td>'
        f'<td>{r["rate_limit_errors"]}</td><td>{r["input_tokens"]}</td><td>{r["output_tokens"]}</td>'
        f'<td>{r["avg_latency"]:.1f}s</td><td>{_e(r["availability"])}</td><td>{_e(r["cost"])}</td></tr>' for r in rows)
    note = ("Token numbers are only what providers reported; requests without reported tokens: "
            f'{sum(r["unreported"] for r in rows)}.')
    return (f'<div class="maac-tiles">{tiles}</div>'
            f'<div class="maac-muted" style="margin-bottom:6px">Skipped: {_e(", ".join(skipped) or "none")}</div>'
            f'<table class="maac-table">{head}{body or "<tr><td colspan=10>No API requests yet.</td></tr>"}</table>'
            f'<div class="maac-muted" style="margin-top:6px">{_e(note)} Cost is not estimated.</div>')


def keys_html(rows: list[tuple[str, str, str, str, str]]) -> str:
    """rows: (agent, provider/model, key (masked), format check, live test)"""
    body = "".join("<tr>" + "".join(f"<td>{_e(c)}</td>" for c in r) + "</tr>" for r in rows)
    return ('<table class="maac-table"><tr><th>Agent</th><th>Provider / model</th><th>Key (masked)</th>'
            f'<th>Format check</th><th>Live test</th></tr>{body}</table>')
