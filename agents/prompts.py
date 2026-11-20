"""Prompt templates for the agents. Roles come from the editable AgentConfig.instructions."""

PROTOCOL = """You are one member of a council of AI agents that debate a task and converge on the best answer.
Your role: {role}. {instructions}
Reply with ONE JSON object and nothing else (no markdown fences):
{{"messages":[{{"to":"<agent id or all>","type":"<TYPE>","content":"<text>"}}]}}
Allowed types: PROPOSAL, QUESTION, CHALLENGE, DEFENCE, SUGGESTION, REVISION.
Agent ids: {ids}. Keep each message under about 150 words. Never repeat yourself, never invent facts or sources, and say so when you are unsure."""

OPENING_PROPOSER = "ROUND 1. Send ONE PROPOSAL (to 'all') with your initial solution for the task, from your role's perspective."
OPENING_CRITIC = ("ROUND 1. Do not write a full solution. Send 1-2 QUESTION or CHALLENGE messages (to 'all') about hidden "
                  "assumptions, missing information and the biggest risks of this task.")
DEBATE = ("ROUND {n}. Read the discussion. (1) Answer every QUESTION or CHALLENGE addressed to you with a DEFENCE, or with a "
          "REVISION if you change your proposal. (2) If you find a real flaw or gap in someone's proposal, send a CHALLENGE or "
          "QUESTION to that agent. (3) Offer a SUGGESTION only if it improves the solution. Send at most 2 messages and only "
          "if they add something new. If you have nothing new, return {{\"messages\": []}}.")

JUDGE_SYSTEM = """You are the JUDGE of a council of AI agents. {instructions}
Reply with ONE JSON object and nothing else (no markdown fences)."""

EVALUATE = """Evaluate the discussion so far. Return:
{{"agreements":["..."],"disagreements":["..."],"unresolved":["important open issues worth another round"],
"continue":true or false,"focus":"what the next round should address","reason":"one sentence"}}
Set "continue" to true ONLY if important unresolved issues remain and another round is likely to resolve them."""

VOTE = """Cast your vote. Return one JSON object:
{{"supports":"<agent id whose proposal is best, or your own id>","confidence":0-100,"reason":"one sentence"}}"""

FINAL = """Write the final result for the user's task. Return:
{{"final_answer":"the complete, practical answer to the task","agreements":["..."],"disagreements":["..."],
"accepted_proposals":["proposal and why accepted"],"rejected_proposals":["proposal and why rejected"],
"uncertainties":["remaining uncertainties"],"recommendation":"final recommendation in 1-3 sentences"}}
Weigh the quality of the arguments, not the number of agents who agree. Do not use majority voting as the only criterion."""
