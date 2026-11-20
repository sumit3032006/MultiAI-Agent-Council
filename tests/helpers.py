"""Shared test helpers: a scripted fake provider so no network or API key is needed."""
import asyncio
import json

import config
from config import Limits
from database.store import Store
from engine.council import Council
from engine.gateway import Gateway
from engine.state import CouncilState
from engine.usage import UsageManager
from providers.base import ProviderError, ProviderResult

TASK = "Design a reliable offline-first note taking app for students."


class Script:
    """Decides what each fake provider answers. Counts calls per provider and records prompts."""

    def __init__(self, eval_continues=1, fail=None, missing=()):
        self.eval_continues = eval_continues   # how many evaluations answer "continue: true"
        self.fail = fail or {}                 # provider -> list of exceptions raised in order, then normal behaviour
        self.missing = set(missing)            # providers without a usable key
        self.calls = {}
        self.prompts = []
        self.evals = 0
        self.counter = 0

    shared_key = None   # set to a string to make every agent use the same key

    def factory(self, agent):
        if agent.id in self.missing:
            raise ProviderError("auth", "API key not usable: missing")
        return FakeAdapter(self, agent.id, self.shared_key or f"sk-or-test-key-{agent.id}")

    def answer(self, provider, system, user):
        self.counter += 1
        n = self.counter
        if "Evaluate the discussion" in user:
            self.evals += 1
            cont = self.evals <= self.eval_continues
            return json.dumps({"agreements": ["use local storage"], "disagreements": ["sync method"],
                               "unresolved": ["how to sync"] if cont else [], "continue": cont,
                               "focus": "settle the sync method", "reason": "test"})
        if "Write the final result" in user:
            return json.dumps({"final_answer": "Use SQLite locally and sync later.", "agreements": ["local first"],
                               "disagreements": ["sync protocol"], "accepted_proposals": ["SQLite + queue"],
                               "rejected_proposals": ["always-online design"], "uncertainties": ["conflict rules"],
                               "recommendation": "Build local-first."})
        if "Cast your vote" in user:
            return json.dumps({"supports": "technical", "confidence": 70, "reason": "most feasible"})
        if "ROUND 1." in user:
            if "Role: Critic" in system or "Your role: Critic" in system:
                return json.dumps({"messages": [{"to": "researcher", "type": "QUESTION",
                                                 "content": f"Which platforms must be supported? (q{n})"}]})
            return json.dumps({"messages": [{"to": "all", "type": "PROPOSAL",
                                             "content": f"Proposal {n} from {provider}: store notes locally and sync later."}]})
        # debate round: reply to whoever addressed us, otherwise challenge someone
        return json.dumps({"messages": [{"to": "critic", "type": "DEFENCE",
                                         "content": f"Defence {n} from {provider}: platforms are Android and web."}]})


class FakeAdapter:
    name = "openrouter"

    def __init__(self, script, provider, api_key):
        self.script, self.provider, self.api_key = script, provider, api_key

    def check_model(self):
        pass

    async def generate(self, system, user, max_tokens=800, temperature=0.6):
        s = self.script
        s.calls[self.provider] = s.calls.get(self.provider, 0) + 1
        s.prompts.append((self.provider, system, user))
        queue = s.fail.get(self.provider)
        if queue:
            err = queue.pop(0)
            if err is not None:
                raise err
        return ProviderResult(s.answer(self.provider, system, user), 10, 20, 0.01)


async def _noop(_):
    return None


async def run_council(script, limits=None, agents=None, stop_when=None, clock=None, store=None):
    limits = limits or Limits(min_interval=0)
    store = store or Store(":memory:")
    usage = UsageManager(on_record=store.add_usage)
    state = CouncilState()
    stop = asyncio.Event()
    gw = Gateway(usage, script.factory, min_interval=0, sleep=_noop)
    kwargs = {"clock": clock} if clock else {}
    council = Council(agents or config.default_agents(), gw, store, usage, state, limits, stop, **kwargs)
    task_id = store.create_task(TASK)
    if stop_when:
        stop_when(stop, state)
    status = await council.run(task_id, TASK)
    return status, council, store, state, usage, task_id
