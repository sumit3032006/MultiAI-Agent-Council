import unittest

import config
from dashboard import render

try:
    import gradio  # noqa
    HAS_GRADIO = True
except ImportError:
    HAS_GRADIO = False


class RenderTests(unittest.TestCase):
    def test_messages_are_escaped_and_show_sender_receiver_round(self):
        msgs = [dict(round=1, sender="critic", receiver="researcher", type="CHALLENGE", content="<script>x</script> why?")]
        out = render.messages_html(msgs, {"critic": "Critic", "researcher": "Researcher"})
        self.assertNotIn("<script>", out)
        for s in ("Critic", "Researcher", "CHALLENGE", "Round 1"):
            self.assertIn(s, out)

    def test_cards_show_five_agents_and_status(self):
        snap = dict(agents={"critic": dict(status="QUOTA EXHAUSTED", activity="x")})
        out = render.agent_cards_html(config.default_agents(), snap, [])
        self.assertEqual(out.count('class="maac-card"'), 5)
        self.assertIn("QUOTA EXHAUSTED", out)

    def test_status_and_analytics(self):
        self.assertIn("Stopped by user", render.status_html(dict(status="stopped")))
        self.assertIn("PROVISIONAL", render.status_html(dict(status="finished", provisional=True)))
        rows = [dict(provider="groq", model="m", requests=2, failures=1, rate_limit_errors=0, input_tokens=5,
                     output_tokens=6, unreported=0, avg_latency=1.2, availability="available", reason="", cost="n/a (free tier)")]
        self.assertIn("groq", render.analytics_html(rows, dict(agents={}), 2, 6, 1))

    def test_keys_table_masks(self):
        self.assertNotIn("sk-or-SECRET", render.keys_html([("Judge", "openrouter / m:free", "...CRET", "configured", "not tested")]))


@unittest.skipUnless(HAS_GRADIO, "gradio is not installed in this environment")
class DashboardStartupTests(unittest.TestCase):
    def test_app_builds(self):
        from dashboard.ui import build_app, create_context
        demo = build_app(create_context(":memory:"))
        self.assertIsNotNone(demo)


if __name__ == "__main__":
    unittest.main()
