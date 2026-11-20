"""Thread-safe live state shared between the council thread and the dashboard."""
from __future__ import annotations

import copy
import threading


class CouncilState:
    def __init__(self):
        self._lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        with self._lock:
            self.data = dict(status="idle", task_id=None, task="", round=0, phase="", stop_reason="",
                             provisional=False, final_markdown="", agents={}, messages=[], error="")

    def update(self, **kw) -> None:
        with self._lock:
            self.data.update(kw)

    def set_agent(self, agent_id: str, **kw) -> None:
        with self._lock:
            self.data["agents"].setdefault(agent_id, {}).update(kw)

    def add_message(self, msg: dict) -> None:
        with self._lock:
            self.data["messages"].append(msg)

    def snapshot(self) -> dict:
        with self._lock:
            return copy.deepcopy(self.data)
