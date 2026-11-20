"""Council orchestrator: adaptive multi-round debate between independent agents."""
from __future__ import annotations

import asyncio
import time
from typing import Callable, Optional

from agents import prompts
from config import AgentConfig, Limits
from database.store import Store
from .gateway import BudgetExceeded, Gateway, ProviderUnavailable
from .messages import Message, MessageType, extract_json, is_duplicate, now_iso, parse_agent_output
from .state import CouncilState
from .usage import UsageManager
from providers.base import ProviderError

MIN_AGENTS = 2
FALLBACK_ORDER = ("technical", "researcher", "innovator", "critic")


class StopRequested(Exception):
    pass


class Council:
    def __init__(self, agents: list[AgentConfig], gateway: Gateway, store: Store, usage: UsageManager,
                 state: CouncilState, limits: Limits, stop: asyncio.Event, clock: Callable[[], float] = time.monotonic):
        self.agents = {a.id: a for a in agents}
        self.gateway, self.store, self.usage, self.state = gateway, store, usage, state
        self.limits, self.stop, self.clock = limits, stop, clock
        self.sem = asyncio.Semaphore(limits.concurrency)
        self.messages: list[dict] = []
        self.focus = ""
        self.provisional = False
        self.evaluators_used: set[str] = set()
        self.round = 0

    # ------------------------------------------------------------ helpers
    def _elapsed(self) -> float:
        return self.clock() - self.t0

    def _check_stop(self) -> None:
        if self.stop.is_set():
            raise StopRequested()

    def _system(self, text: str) -> None:
        self._emit("system", "all", MessageType.SYSTEM, text, store=True)

    def _emit(self, sender: str, receiver: str, mtype: MessageType, content: str, store: bool = True) -> Optional[dict]:
        try:
            m = Message(task_id=self.task_id, sender=sender, receiver=receiver, type=mtype,
                        content=content, round=self.round)
        except ValueError:
            return None
        if store:
            self.store.add_message(m)
        d = m.model_dump()
        d["type"] = m.type.value
        self.state.add_message(d)
        if mtype is not MessageType.SYSTEM:
            self.messages.append(d)
        return d

    def _name(self, agent_id: str) -> str:
        a = self.agents.get(agent_id)
        return a.name if a else agent_id

    def _debaters(self) -> list[AgentConfig]:
        return [a for a in self.agents.values() if a.id != "judge" and self._usable(a)]

    def _usable(self, a: AgentConfig) -> bool:
        return a.enabled and self.gateway.check(a) is None

    def _set(self, agent: AgentConfig, **kw) -> None:
        problem = self.gateway.check(agent)
        self.state.set_agent(agent.id, api=("available" if problem is None else problem[0]), **kw)

    def _refresh_status(self) -> None:
        for a in self.agents.values():
            if not a.enabled:
                self.state.set_agent(a.id, status="DISABLED", activity="Disabled in settings", api="-")
                continue
            problem = self.gateway.check(a)
            if problem is None:
                self._set(a, status="IDLE", activity="Ready")
            else:
                kind, why = problem
                label = "QUOTA EXHAUSTED" if kind == "quota_exhausted" else "OFFLINE"
                self._set(a, status=label, activity=why[:140])

    def _limit_reason(self, needed: int) -> Optional[str]:
        if self._elapsed() > 0.8 * self.limits.max_seconds:
            return "time limit reached"
        if self.usage.task_requests + needed + 1 > self.usage.request_cap:   # +1 reserves the final answer
            return "request limit reached"
        if self.usage.task_output_tokens >= self.limits.max_output_tokens:
            return "token limit reached"
        return None

    async def _ask(self, agent: AgentConfig, system: str, user: str, max_tokens: int, activity: str) -> Optional[str]:
        """One guarded API call. Returns text, or None if the agent had to be skipped."""
        self._check_stop()
        self._set(agent, status="WORKING", activity=activity)
        remaining = self.limits.max_seconds - self._elapsed()
        if remaining <= 0:
            self._set(agent, status="FAILED", activity="time limit reached")
            return None
        call = asyncio.ensure_future(self.gateway.call(agent, system, user, max_tokens))
        stopper = asyncio.ensure_future(self.stop.wait())
        try:
            done, _ = await asyncio.wait({call, stopper}, timeout=remaining, return_when=asyncio.FIRST_COMPLETED)
        except asyncio.CancelledError:
            call.cancel(); stopper.cancel()
            raise
        stopper.cancel()
        if stopper in done:
            call.cancel()
            raise StopRequested()
        if call not in done:
            call.cancel()
            self._set(agent, status="FAILED", activity="time limit reached")
            return None
        try:
            res = call.result()
        except ProviderUnavailable as e:
            label = "QUOTA EXHAUSTED" if e.kind == "quota_exhausted" else "OFFLINE"
            self._set(agent, status=label, activity=str(e)[:140])
            self._system(f"{agent.name} skipped: {label.lower()} ({agent.provider}).")
        except BudgetExceeded as e:
            self._set(agent, status="IDLE", activity=str(e))
        except ProviderError as e:
            if e.kind == "quota":
                self._set(agent, status="QUOTA EXHAUSTED", activity=str(e)[:140])
                self._system(f"{agent.name} skipped: QUOTA EXHAUSTED ({agent.provider}). Continuing with the other agents.")
            elif e.kind in ("auth", "bad_model"):
                self._set(agent, status="OFFLINE", activity=str(e)[:140])
                self._system(f"{agent.name} offline: {e}")
            else:
                self._set(agent, status="FAILED", activity=str(e)[:140])
                self._system(f"{agent.name} failed this turn: {e}")
        else:
            self._set(agent, status="IDLE", activity="Waiting", last_latency=round(res.latency, 2))
            return res.text
        return None

    # ------------------------------------------------------------ context
    def _latest_proposals(self) -> dict[str, dict]:
        latest: dict[str, dict] = {}
        for m in self.messages:
            if m["type"] in ("PROPOSAL", "REVISION"):
                latest[m["sender"]] = m
        return latest

    def _context(self, agent_id: str, budget: int = 7000) -> str:
        parts = [f"TASK:\n{self.task_text}"]
        props = self._latest_proposals()
        if props:
            parts.append("CURRENT PROPOSALS:\n" + "\n".join(
                f"- {self._name(s)} ({m['type']}): {m['content'][:900]}" for s, m in props.items()))
        prev = [m for m in self.messages if m["round"] == self.round - 1 and m["type"] not in ("PROPOSAL", "REVISION")
                and m["sender"] != "system"]
        prev += [m for m in self.messages if m["round"] == self.round - 1 and m["type"] in ("PROPOSAL", "REVISION")
                 and m["receiver"] not in ("all",)]
        if prev:
            lines = []
            for m in prev:
                to = "YOU" if m["receiver"] == agent_id else ("ALL" if m["receiver"] == "all" else self._name(m["receiver"]))
                lines.append(f"- {self._name(m['sender'])} -> {to} [{m['type']}]: {m['content'][:500]}")
            parts.append(f"DISCUSSION IN ROUND {self.round - 1}:\n" + "\n".join(lines))
        if self.focus:
            parts.append(f"JUDGE'S FOCUS FOR THIS ROUND: {self.focus}")
        text = "\n\n".join(parts)
        return text if len(text) <= budget else text[:budget] + "\n[context truncated]"

    def _protocol(self, agent: AgentConfig) -> str:
        ids = ", ".join(a for a in self.agents if a != agent.id) + ", all"
        return prompts.PROTOCOL.format(role=agent.role, instructions=agent.instructions, ids=ids)

    # ------------------------------------------------------------ rounds
    async def _turn(self, agent: AgentConfig) -> int:
        if self.round == 1:
            instr = prompts.OPENING_CRITIC if agent.id == "critic" else prompts.OPENING_PROPOSER
            default = "QUESTION" if agent.id == "critic" else "PROPOSAL"
        else:
            instr = prompts.DEBATE.format(n=self.round)
            default = "SUGGESTION"
        user = f"{self._context(agent.id)}\n\n{instr}"
        async with self.sem:
            text = await self._ask(agent, self._protocol(agent), user, self.limits.per_call_tokens,
                                   f"Round {self.round}: thinking")
        if text is None:
            return 0
        valid = set(self.agents) | {"all"}
        parsed = parse_agent_output(text, agent.id, valid, default_type=default)
        if self.round == 1 and agent.id != "critic" and parsed and not any(t == "PROPOSAL" for _, t, _ in parsed):
            parsed[0] = (parsed[0][0], "PROPOSAL", parsed[0][2])
        added = 0
        mine = [m["content"] for m in self.messages if m["sender"] == agent.id]
        for receiver, mtype, content in parsed:
            if is_duplicate(content, mine):
                continue
            if self._emit(agent.id, receiver, MessageType(mtype), content):
                mine.append(content)
                added += 1
        return added

    async def _judge_call(self, system: str, user: str, max_tokens: int, activity: str):
        """Try the Judge, then fall back to other agents (result becomes provisional)."""
        order = ["judge"] + [i for i in FALLBACK_ORDER if i in self.agents]
        for agent_id in order:
            agent = self.agents[agent_id]
            if not self._usable(agent):
                continue
            async with self.sem:
                text = await self._ask(agent, system, user, max_tokens, activity)
            if text is not None:
                if agent_id != "judge":
                    self.provisional = True
                    self.state.update(provisional=True)
                self.evaluators_used.add(agent_id)
                return text, agent
        return None

    def _digest(self) -> str:
        lines = []
        for s, m in self._latest_proposals().items():
            lines.append(f"{self._name(s)} ({m['type']}): {m['content'][:900]}")
        recent = [m for m in self.messages if m["type"] not in ("PROPOSAL", "REVISION", "EVALUATION", "VOTE", "FINAL")]
        for m in recent[-12:]:
            lines.append(f"[r{m['round']}] {self._name(m['sender'])} -> {self._name(m['receiver'])} {m['type']}: {m['content'][:400]}")
        return "\n".join(lines)

    async def _evaluate(self) -> Optional[dict]:
        judge = self.agents["judge"]
        system = prompts.JUDGE_SYSTEM.format(instructions=judge.instructions)
        user = f"TASK:\n{self.task_text}\n\nDISCUSSION SO FAR (round {self.round}):\n{self._digest()}\n\n{prompts.EVALUATE}"
        got = await self._judge_call(system, user, self.limits.per_call_tokens, f"Round {self.round}: evaluating")
        if got is None:
            return None
        text, agent = got
        data = extract_json(text) or {}
        unresolved = [str(x) for x in data.get("unresolved", []) if str(x).strip()] if isinstance(data.get("unresolved"), list) else []
        ev = dict(agreements=data.get("agreements") or [], disagreements=data.get("disagreements") or [],
                  unresolved=unresolved, focus=str(data.get("focus", ""))[:400],
                  **{"continue": bool(data.get("continue")) and bool(unresolved)}, reason=str(data.get("reason", ""))[:300])
        tag = "[PROVISIONAL] " if agent.id != "judge" else ""
        summary = (f"{tag}Agreements: {len(ev['agreements'])}; disagreements: {len(ev['disagreements'])}; "
                   f"unresolved: {len(unresolved)}. Another round: {'yes' if ev['continue'] else 'no'}. {ev['reason']}")
        if unresolved:
            summary += "\nOpen issues: " + "; ".join(unresolved)[:600]
        self._emit(agent.id, "all", MessageType.EVALUATION, summary)
        return ev

    async def _vote_round(self) -> None:
        voters = self._debaters()
        if not voters or self._limit_reason(len(voters)):
            return
        self.state.update(phase="Voting")
        valid = set(self.agents)

        async def vote(agent):
            user = f"{self._context(agent.id)}\n\n{prompts.VOTE}"
            async with self.sem:
                text = await self._ask(agent, self._protocol(agent), user, 200, "Voting")
            data = extract_json(text or "")
            if not data:
                return
            supports = str(data.get("supports", agent.id)).strip().lower()
            supports = supports if supports in valid else agent.id
            try:
                conf = max(0, min(100, int(data.get("confidence", 50))))
            except (TypeError, ValueError):
                conf = 50
            reason = str(data.get("reason", ""))[:300] or "no reason given"
            self._emit(agent.id, "judge", MessageType.VOTE,
                       f"Supports: {self._name(supports)} (confidence {conf}%). {reason}")

        await asyncio.gather(*(vote(a) for a in voters))

    # ------------------------------------------------------------ final
    def _render_final(self, data: dict, evaluator: Optional[AgentConfig], stop_reason: str) -> str:
        def bullets(key):
            items = data.get(key) or []
            items = items if isinstance(items, list) else [items]
            return "\n".join(f"- {str(i)}" for i in items) or "- (none reported)"

        out = []
        if self.provisional and evaluator:
            out.append(f"> **PROVISIONAL RESULT** - the Judge was unavailable, so {evaluator.name} evaluated instead.\n")
        out.append("## Final answer\n" + str(data.get("final_answer") or "(no answer text returned)"))
        out.append("## Areas of agreement\n" + bullets("agreements"))
        out.append("## Areas of disagreement\n" + bullets("disagreements"))
        out.append("## Accepted proposals\n" + bullets("accepted_proposals"))
        out.append("## Rejected proposals\n" + bullets("rejected_proposals"))
        out.append("## Remaining uncertainties\n" + bullets("uncertainties"))
        out.append("## Final recommendation\n" + str(data.get("recommendation") or "(none)"))
        out.append(f"---\n*Discussion rounds: {self.round}. Stopped because: {stop_reason}.*")
        return "\n\n".join(out)

    def _unreviewed(self, stop_reason: str) -> str:
        return ("> **NOT FULLY REVIEWED** - no agent was available to act as Judge, so this is only a digest of the "
                "discussion, not an evaluated answer.\n\n## Latest proposals and discussion\n" + (self._digest() or "(no messages)")
                + f"\n\n---\n*Discussion rounds: {self.round}. Stopped because: {stop_reason}.*")

    async def _final(self, stop_reason: str) -> str:
        self.state.update(phase="Writing final answer")
        judge = self.agents["judge"]
        system = prompts.JUDGE_SYSTEM.format(instructions=judge.instructions)
        user = f"TASK:\n{self.task_text}\n\nDISCUSSION:\n{self._digest()}\n\n{prompts.FINAL}"
        got = await self._judge_call(system, user, min(self.limits.per_call_tokens * 2, 4000), "Writing final answer")
        if got is None:
            return self._unreviewed(stop_reason)
        text, agent = got
        data = extract_json(text)
        if data is None:
            data = {"final_answer": text, "uncertainties": ["The evaluator did not return structured sections."]}
        md = self._render_final(data, agent, stop_reason)
        self._emit(agent.id, "all", MessageType.FINAL, md[:7900])
        return md

    # ------------------------------------------------------------ main
    async def run(self, task_id: str, task_text: str) -> str:
        """Returns the status: finished | stopped | failed."""
        self.task_id, self.task_text, self.t0 = task_id, task_text, self.clock()
        self.usage.begin_task(task_id, self.limits.max_requests)
        self.state.update(status="running", task_id=task_id, task=task_text, round=0, phase="Starting",
                          final_markdown="", stop_reason="", provisional=False, error="")
        stop_reason = "finished"
        try:
            self._refresh_status()
            available = [a for a in self.agents.values() if self._usable(a)]
            if len(available) < MIN_AGENTS:
                msg = (f"Only {len(available)} agent(s) available; the council needs at least {MIN_AGENTS}. "
                       "Check your API keys in .env and the agent status cards.")
                self._system(msg)
                self.state.update(status="failed", error=msg, stop_reason=msg, final_markdown=f"**Could not start.** {msg}")
                self.store.finish_task(task_id, "failed", msg, False, msg)
                return "failed"
            await self._debate_loop()
            stop_reason = self.state.snapshot()["stop_reason"] or "finished"
            if not self.messages:
                msg = "No agent produced a usable answer. See the system notices in the discussion."
                self.state.update(status="failed", error=msg, final_markdown=f"**No result.** {msg}")
                self.store.finish_task(task_id, "failed", msg, False, msg)
                return "failed"
            if self.limits.enable_votes:
                await self._vote_round()
            final_md = await self._final(stop_reason)
            self.state.update(status="finished", final_markdown=final_md, phase="Done")
            self.store.finish_task(task_id, "finished", final_md, self.provisional, stop_reason)
            return "finished"
        except StopRequested:
            msg = "Stopped by the user. The discussion so far has been saved."
            self._system(msg)
            self.state.update(status="stopped", stop_reason="stopped by user", phase="Stopped",
                             final_markdown=f"**{msg}**\n\n## Discussion digest\n{self._digest()}")
            self.store.finish_task(task_id, "stopped", "", self.provisional, "stopped by user")
            return "stopped"
        finally:
            for a in self.agents.values():
                snap = self.state.snapshot()["agents"].get(a.id, {})
                if snap.get("status") in ("WORKING",):
                    self.state.set_agent(a.id, status="IDLE", activity="Waiting")

    async def _debate_loop(self) -> None:
        L = self.limits
        for rnd in range(1, L.max_rounds + 1):
            self._check_stop()
            deb = self._debaters()
            if not deb:
                self.state.update(stop_reason="no debating agents available")
                return
            self.round = rnd
            if rnd > 1:
                reason = self._limit_reason(len(deb) + 1)
                if reason:
                    self.state.update(stop_reason=reason)
                    self._system(f"Discussion stopped: {reason}.")
                    return
            self.state.update(round=rnd, phase=f"Round {rnd}: agents are discussing")
            counts = await asyncio.gather(*(self._turn(a) for a in deb))
            self._check_stop()
            if sum(counts) == 0:
                self.state.update(stop_reason="no new arguments (discussion stalled)" if rnd > 1 else "no agent responded")
                return
            if rnd == L.max_rounds:
                self.state.update(stop_reason="maximum discussion rounds reached")
                return
            if self._limit_reason(1):
                self.state.update(stop_reason=self._limit_reason(1))
                return
            self.state.update(phase=f"Round {rnd}: Judge is evaluating")
            ev = await self._evaluate()
            self._check_stop()
            if ev is None:
                self.state.update(stop_reason="no evaluator available to continue the discussion")
                return
            if not ev["continue"]:
                self.state.update(stop_reason="Judge decided the discussion is sufficient")
                return
            self.focus = ev["focus"]
