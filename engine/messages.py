"""Message model, parsing of agent output and duplicate detection."""
from __future__ import annotations

import difflib
import json
import re
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class MessageType(str, Enum):
    PROPOSAL = "PROPOSAL"
    QUESTION = "QUESTION"
    CHALLENGE = "CHALLENGE"
    DEFENCE = "DEFENCE"
    SUGGESTION = "SUGGESTION"
    REVISION = "REVISION"
    EVALUATION = "EVALUATION"
    VOTE = "VOTE"
    FINAL = "FINAL"
    SYSTEM = "SYSTEM"  # notices from the orchestrator (skipped agent, limits); not an agent message


AGENT_TYPES = {"PROPOSAL", "QUESTION", "CHALLENGE", "DEFENCE", "SUGGESTION", "REVISION"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Message(BaseModel):
    task_id: str = Field(min_length=1)
    sender: str = Field(min_length=1)
    receiver: str = Field(min_length=1)
    type: MessageType
    content: str = Field(min_length=1, max_length=8000)
    timestamp: str = Field(default_factory=now_iso)
    round: int = Field(0, ge=0)


def extract_json(text: str) -> Optional[dict]:
    """Pull the first JSON object out of a model answer (tolerates ``` fences and chatter)."""
    if not text:
        return None
    cleaned = re.sub(r"```(?:json)?", "", text)
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        data = json.loads(cleaned[start:end + 1])
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def parse_agent_output(text: str, sender: str, valid_receivers: set[str],
                       default_type: str = "SUGGESTION", max_messages: int = 2,
                       max_chars: int = 1500) -> list[tuple[str, str, str]]:
    """Return [(receiver, type, content)]. Invalid receivers become 'all'; unknown types use the default."""
    data = extract_json(text)
    out: list[tuple[str, str, str]] = []
    if data is None or not isinstance(data.get("messages"), list):
        content = (text or "").strip()
        return [("all", default_type, content[:max_chars])] if len(content) >= 15 else []
    for item in data["messages"][:max_messages]:
        if not isinstance(item, dict):
            continue
        content = str(item.get("content", "")).strip()
        if len(content) < 15:
            continue  # meaningless
        receiver = str(item.get("to", "all")).strip().lower()
        if receiver == sender or receiver not in valid_receivers:
            receiver = "all"
        mtype = str(item.get("type", default_type)).strip().upper().replace("DEFENSE", "DEFENCE")
        if mtype not in AGENT_TYPES:
            mtype = default_type
        out.append((receiver, mtype, content[:max_chars]))
    return out


def _norm(s: str) -> str:
    return re.sub(r"\W+", " ", s.lower()).strip()[:600]


def is_duplicate(content: str, previous: list[str], threshold: float = 0.88) -> bool:
    n = _norm(content)
    return any(difflib.SequenceMatcher(None, n, _norm(p)).ratio() >= threshold for p in previous)
