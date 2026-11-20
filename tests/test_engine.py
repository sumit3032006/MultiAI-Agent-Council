import asyncio
import unittest

import config
from config import Limits
from database.store import Store
from engine.messages import Message, MessageType, extract_json, is_duplicate, parse_agent_output
from engine.gateway import BudgetExceeded, Gateway, ProviderUnavailable
from engine.usage import Availability, UsageManager
from providers.base import ProviderError
from engine.gateway import scope_label
from tests.helpers import Script, run_council, _noop, TASK


def go(coro):
    return asyncio.run(coro)


class MessageTests(unittest.TestCase):
    def test_message_validation(self):
        ok = Message(task_id="t", sender="a", receiver="b", type="QUESTION", content="why?", round=1)
        self.assertEqual(ok.type, MessageType.QUESTION)
        with self.assertRaises(ValueError):
            Message(task_id="t", sender="a", receiver="b", type="QUESTION", content="")
        with self.assertRaises(ValueError):
            Message(task_id="t", sender="a", receiver="b", type="NONSENSE", content="x")

    def test_routing_rules(self):
        valid = {"researcher", "critic", "all"}
        text = '{"messages":[{"to":"critic","type":"challenge","content":"this claim lacks evidence"},' \
               '{"to":"ghost","type":"weird","content":"another useful remark here"},' \
               '{"to":"all","type":"SUGGESTION","content":"third message is dropped"}]}'
        out = parse_agent_output(text, "researcher", valid)
        self.assertEqual(len(out), 2)                      # max two messages
        self.assertEqual(out[0][:2], ("critic", "CHALLENGE"))
        self.assertEqual(out[1][:2], ("all", "SUGGESTION"))  # unknown receiver -> all, unknown type -> default
        self.assertEqual(parse_agent_output(text, "critic", valid)[0][0], "all")  # cannot message yourself

    def test_plain_text_and_empty(self):
        self.assertEqual(len(parse_agent_output("A long enough plain answer.", "x", {"all"})), 1)
        self.assertEqual(parse_agent_output('{"messages": []}', "x", {"all"}), [])
        self.assertEqual(extract_json('```json\n{"a": 1}\n```'), {"a": 1})

    def test_duplicates(self):
        self.assertTrue(is_duplicate("Use SQLite for local storage.", ["use sqlite for local storage"]))
        self.assertFalse(is_duplicate("Use SQLite for local storage.", ["Prefer a cloud database with sync"]))


class StoreTests(unittest.TestCase):
    def test_persistence_and_deletion(self):
        import tempfile, os
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "x.db")
            s = Store(path)
            t = s.create_task("task one is here")
            s.add_message(Message(task_id=t, sender="a", receiver="all", type="PROPOSAL", content="first idea", round=1))
            s.add_message(Message(task_id=t, sender="a", receiver="all", type="REVISION", content="better idea", round=2))
            s.add_usage(dict(task_id=t, provider="researcher", model="m", ok=True, input_tokens=1, output_tokens=2, latency=0.1))
            s.finish_task(t, "finished", "answer", True, "done")
            s2 = Store(path)   # new connection: data really persisted
            self.assertEqual(len(s2.get_messages(t)), 2)
            self.assertEqual([m["type"] for m in s2.get_proposals(t)], ["PROPOSAL", "REVISION"])
            self.assertEqual(s2.get_task(t)["provisional"], 1)
            self.assertEqual(s2.usage_for_task(t)[0]["output_tokens"], 2)
            t2 = s2.create_task("another task here")
            self.assertEqual(s2.delete_tasks([t]), 1)
            self.assertIsNone(s2.get_task(t))
            self.assertEqual(s2.get_messages(t), [])
            self.assertIsNotNone(s2.get_task(t2))
            s2.clear_all()
            self.assertEqual(s2.list_tasks(), [])


class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.agent = config.default_agents()[0]

    def test_single_retry_on_temporary_failure(self):
        sc = Script(fail={"researcher": [ProviderError("temporary", "boom")]})
        usage = UsageManager()
        gw = Gateway(usage, sc.factory, 0, sleep=_noop)
        res = go(gw.call(self.agent, "s", "u", 10))
        self.assertTrue(res.text)
        self.assertEqual(sc.calls["researcher"], 2)          # one failure + exactly one retry
        self.assertEqual(next(iter(usage.stats.values())).failures, 1)

    def test_no_second_retry(self):
        sc = Script(fail={"researcher": [ProviderError("temporary", "a"), ProviderError("temporary", "b"), ProviderError("temporary", "c")]})
        gw = Gateway(UsageManager(), sc.factory, 0, sleep=_noop)
        with self.assertRaises(ProviderError):
            go(gw.call(self.agent, "s", "u", 10))
        self.assertEqual(sc.calls["researcher"], 2)

    def test_quota_marks_exhausted_and_is_not_called_again(self):
        sc = Script(fail={"researcher": [ProviderError("quota", "daily quota")]})
        usage = UsageManager()
        gw = Gateway(usage, sc.factory, 0, sleep=_noop)
        with self.assertRaises(ProviderError):
            go(gw.call(self.agent, "s", "u", 10))
        self.assertEqual(usage.availability(scope_label(sc.factory(self.agent)))[0], Availability.QUOTA_EXHAUSTED)
        with self.assertRaises(ProviderUnavailable):
            go(gw.call(self.agent, "s", "u", 10))
        self.assertEqual(sc.calls["researcher"], 1)           # the exhausted API was never called again

    def test_rate_limit_cooldown(self):
        sc = Script(fail={"researcher": [ProviderError("rate_limit", "x"), ProviderError("rate_limit", "y")]})
        usage = UsageManager()
        gw = Gateway(usage, sc.factory, 0, sleep=_noop)
        with self.assertRaises(ProviderError):
            go(gw.call(self.agent, "s", "u", 10))
        self.assertEqual(usage.availability(scope_label(sc.factory(self.agent)))[0], Availability.COOLING_DOWN)
        self.assertEqual(next(iter(usage.stats.values())).rate_limit_errors, 2)

    def test_request_cap(self):
        sc = Script()
        usage = UsageManager()
        usage.begin_task("t", 1)
        gw = Gateway(usage, sc.factory, 0, sleep=_noop)
        go(gw.call(self.agent, "s", "u", 10))
        with self.assertRaises(BudgetExceeded):
            go(gw.call(self.agent, "s", "u", 10))

    def test_usage_tracking_and_unreported_tokens(self):
        u = UsageManager()
        u.begin_task("t", 10)
        u.record("a", "m", True, None, None, 0.5)
        u.record("a", "m", True, 3, 4, 0.5)
        row = u.summary_rows()[0]
        self.assertEqual((row["requests"], row["input_tokens"], row["output_tokens"], row["unreported"]), (2, 3, 4, 1))
        self.assertEqual(row["cost"], "n/a (free tier)")


class CouncilTests(unittest.TestCase):
    def test_full_run_with_communication(self):
        sc = Script(eval_continues=1)
        status, council, store, state, usage, tid = go(run_council(sc))
        self.assertEqual(status, "finished")
        msgs = store.get_messages(tid)
        types = {m["type"] for m in msgs}
        self.assertTrue({"PROPOSAL", "QUESTION", "DEFENCE", "EVALUATION", "VOTE", "FINAL"} <= types)
        q = next(m for m in msgs if m["type"] == "QUESTION")
        self.assertEqual((q["sender"], q["receiver"], q["round"]), ("critic", "researcher", 1))
        # the question to the researcher must be shown to the researcher (marked "YOU") in round 2
        r2 = [u for p, s, u in sc.prompts if p == "researcher" and "ROUND 2." in u]
        self.assertTrue(r2 and "-> YOU [QUESTION]" in r2[0])
        # other agents see it too (shared memory) but not marked as addressed to them
        r2c = [u for p, s, u in sc.prompts if p == "technical" and "ROUND 2." in u]
        self.assertIn("-> Researcher [QUESTION]", r2c[0])
        final = store.get_task(tid)
        for heading in ("Areas of agreement", "Areas of disagreement", "Accepted proposals", "Rejected proposals",
                        "Remaining uncertainties", "Final recommendation"):
            self.assertIn(heading, final["final_answer"])
        self.assertEqual(final["provisional"], 0)
        self.assertEqual(state.snapshot()["status"], "finished")

    def test_adaptive_rounds(self):
        _, c0, *_ = go(run_council(Script(eval_continues=0)))
        _, c2, *_ = go(run_council(Script(eval_continues=2)))
        self.assertEqual(c0.round, 1)    # judge says: enough after round 1
        self.assertEqual(c2.round, 3)    # judge asked for two more rounds, then stopped

    def test_max_rounds_limit(self):
        status, c, *_ , tid = go(run_council(Script(eval_continues=99), Limits(max_rounds=2, min_interval=0)))
        self.assertEqual(c.round, 2)
        self.assertIn("maximum discussion rounds", c.state.snapshot()["stop_reason"])

    def test_max_requests_limit_is_respected(self):
        sc = Script(eval_continues=99)
        status, c, store, state, usage, tid = go(run_council(sc, Limits(max_requests=10, max_rounds=10, min_interval=0)))
        self.assertEqual(status, "finished")
        self.assertLessEqual(usage.task_requests, 10)
        self.assertIn("request limit", state.snapshot()["stop_reason"])
        self.assertIn("Final answer", store.get_task(tid)["final_answer"])   # final answer still written

    def test_token_limit(self):
        # each fake call reports 20 output tokens; a 1000-token ceiling is hit only after ~50 calls, so
        # shrink the ceiling on the council directly to prove the stop condition works
        sc = Script(eval_continues=99)
        lim = Limits(max_rounds=10, min_interval=0)
        object.__setattr__(lim, "max_output_tokens", 60)
        status, c, store, state, usage, tid = go(run_council(sc, lim))
        self.assertEqual(status, "finished")
        self.assertIn("token limit", state.snapshot()["stop_reason"])

    def test_time_limit_with_fake_clock(self):
        t = {"now": 0.0}
        def clock():
            t["now"] += 100.0       # every look at the clock "costs" 100 seconds
            return t["now"]
        status, c, *_ = go(run_council(Script(eval_continues=99), Limits(max_seconds=30, min_interval=0), clock=clock))
        self.assertIn(status, ("finished", "failed"))
        self.assertTrue(c.round <= 2)

    def test_manual_stop_preserves_history(self):
        def stop_after_round_one(stop, state):
            async def watcher():
                while True:
                    await asyncio.sleep(0)
                    if state.snapshot()["round"] >= 2:
                        stop.set(); return
            asyncio.ensure_future(watcher())
        sc = Script(eval_continues=99)
        status, c, store, state, usage, tid = go(run_council(sc, stop_when=stop_after_round_one))
        self.assertEqual(status, "stopped")
        self.assertEqual(store.get_task(tid)["status"], "stopped")
        self.assertGreater(len(store.get_messages(tid)), 0)    # completed discussion is kept
        self.assertNotIn("Write the final result", " ".join(u for _, _, u in sc.prompts))

    def test_stop_before_start(self):
        status, *_ = go(run_council(Script(), stop_when=lambda stop, state: stop.set()))
        self.assertEqual(status, "stopped")

    def test_quota_exhausted_agent_is_skipped(self):
        sc = Script(eval_continues=2, fail={"critic": [ProviderError("quota", "daily quota used up")]})
        status, c, store, state, usage, tid = go(run_council(sc))
        self.assertEqual(status, "finished")
        self.assertEqual(sc.calls["critic"], 1)                 # never called again after exhaustion
        self.assertEqual(state.snapshot()["agents"]["critic"]["status"], "QUOTA EXHAUSTED")
        self.assertEqual(usage.availability(scope_label(sc.factory(config.default_agents()[2])))[0], Availability.QUOTA_EXHAUSTED)
        self.assertTrue(any(m["type"] == "SYSTEM" and "QUOTA EXHAUSTED" in m["content"] for m in store.get_messages(tid)))

    def test_agents_sharing_one_key_share_quota_state(self):
        sc = Script(eval_continues=0, fail={"researcher": [ProviderError("quota", "free-models-per-day")]})
        sc.shared_key = "sk-or-shared-key-for-everyone"
        status, c, store, state, usage, tid = go(run_council(sc))
        # the single shared key is exhausted after the first quota error: no other agent may call it again
        self.assertEqual(sum(sc.calls.values()), 1)
        self.assertEqual(status, "failed")

    def test_unavailable_agent_missing_key(self):
        sc = Script(missing={"innovator"})
        status, c, store, state, usage, tid = go(run_council(sc))
        self.assertEqual(status, "finished")
        self.assertNotIn("innovator", sc.calls)
        self.assertEqual(state.snapshot()["agents"]["innovator"]["status"], "OFFLINE")

    def test_judge_fallback_is_provisional(self):
        sc = Script(missing={"judge"})
        status, c, store, state, usage, tid = go(run_council(sc))
        self.assertEqual(status, "finished")
        task = store.get_task(tid)
        self.assertEqual(task["provisional"], 1)
        self.assertIn("PROVISIONAL", task["final_answer"])
        final_msg = next(m for m in store.get_messages(tid) if m["type"] == "FINAL")
        self.assertNotEqual(final_msg["sender"], "judge")

    def test_judge_quota_midway_falls_back(self):
        sc = Script(eval_continues=0, fail={"judge": [ProviderError("quota", "daily quota")]})
        status, c, store, *_ , tid = go(run_council(sc))
        self.assertTrue(store.get_task(tid)["provisional"])

    def test_no_evaluator_reports_not_reviewed(self):
        agents = config.default_agents()
        agents = [a.model_copy(update={"enabled": a.id in ("researcher", "critic")}) if hasattr(a, "model_copy") else a for a in agents]
        # only researcher + critic enabled; both can debate and one of them is the fallback evaluator,
        # so make every evaluator candidate fail: the final result must say it was not reviewed
        sc = Script(eval_continues=0, fail={"researcher": [None, ProviderError("quota", "x")],
                                            "critic": [None, ProviderError("quota", "x")]})
        status, c, store, state, usage, tid = go(run_council(sc, agents=agents, limits=Limits(min_interval=0, enable_votes=False)))
        self.assertEqual(status, "finished")
        self.assertIn("NOT FULLY REVIEWED", store.get_task(tid)["final_answer"])

    def test_too_few_agents_fails_cleanly(self):
        sc = Script(missing={"researcher", "technical", "critic", "innovator"})
        status, c, store, state, usage, tid = go(run_council(sc))
        self.assertEqual(status, "failed")
        self.assertEqual(store.get_task(tid)["status"], "failed")
        self.assertIn("at least", state.snapshot()["error"])

    def test_no_repeated_messages(self):
        sc = Script(eval_continues=3)
        # an agent that repeats exactly the same text every round must not flood the discussion
        original = sc.answer
        def repeating(provider, system, user):
            if provider == "researcher" and "ROUND" in user and "ROUND 1." not in user:
                return '{"messages":[{"to":"all","type":"SUGGESTION","content":"Use a queue for offline sync operations."}]}'
            return original(provider, system, user)
        sc.answer = repeating
        status, c, store, *_ , tid = go(run_council(sc, Limits(max_rounds=4, min_interval=0)))
        mine = [m for m in store.get_messages(tid) if m["sender"] == "researcher" and m["type"] == "SUGGESTION"]
        self.assertEqual(len(mine), 1)

    def test_runner_thread_end_to_end(self):
        from engine.runner import CouncilRunner, clean_task_text
        from engine.state import CouncilState
        store = Store(":memory:")
        usage = UsageManager(on_record=store.add_usage)
        state = CouncilState()
        sc = Script(eval_continues=0)
        runner = CouncilRunner(store, usage, state, sc.factory)
        tid = runner.start(TASK, Limits(min_interval=0), config.default_agents())
        runner.join(20)
        self.assertEqual(state.snapshot()["status"], "finished")
        self.assertEqual(store.get_task(tid)["status"], "finished")
        import threading, time
        blocker = threading.Thread(target=time.sleep, args=(0.3,)); blocker.start()
        runner._thread = blocker                       # pretend a council is running
        with self.assertRaises(RuntimeError):
            runner.start(TASK, Limits(min_interval=0), config.default_agents())
        blocker.join()
        with self.assertRaises(ValueError):
            clean_task_text("short")
        with self.assertRaises(ValueError):
            clean_task_text("x" * 4001)


if __name__ == "__main__":
    unittest.main()
