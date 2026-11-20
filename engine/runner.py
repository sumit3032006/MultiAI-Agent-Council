"""Runs a council in a background thread so the dashboard stays responsive."""
from __future__ import annotations

import asyncio
import threading
from typing import Callable, Optional

from config import AgentConfig, Limits
from database.store import Store
from .council import Council
from .gateway import Gateway
from .messages import MessageType
from .state import CouncilState
from .usage import UsageManager
from pydantic import BaseModel, Field


class TaskRequest(BaseModel):
    text: str = Field(min_length=10, max_length=4000)


def clean_task_text(raw: str) -> str:
    """Validate the user's task. Raises ValueError with a friendly message."""
    text = "".join(ch for ch in (raw or "") if ch == "\n" or ch >= " ").strip()
    if len(text) < 10:
        raise ValueError("Please describe your task in at least 10 characters.")
    if len(text) > 4000:
        raise ValueError("The task is too long (maximum 4000 characters).")
    return TaskRequest(text=text).text


class CouncilRunner:
    def __init__(self, store: Store, usage: UsageManager, state: CouncilState, adapter_factory: Callable):
        self.store, self.usage, self.state, self.adapter_factory = store, usage, state, adapter_factory
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._stop_event: Optional[asyncio.Event] = None
        self._stop_flag = threading.Event()

    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self, raw_text: str, limits: Limits, agents: list[AgentConfig]) -> str:
        if self.is_running():
            raise RuntimeError("A council is already running. Press Stop first.")
        text = clean_task_text(raw_text)
        task_id = self.store.create_task(text)
        self._stop_flag.clear()
        self.state.reset()
        self.state.update(status="running", task_id=task_id, task=text, phase="Starting")
        self._thread = threading.Thread(target=self._thread_main, args=(task_id, text, limits, agents), daemon=True)
        self._thread.start()
        return task_id

    def stop(self) -> None:
        self._stop_flag.set()
        if self.is_running():
            self.state.update(status="stopping", phase="Stopping...")
            if self._loop and self._stop_event:
                self._loop.call_soon_threadsafe(self._stop_event.set)

    def join(self, timeout: Optional[float] = None) -> None:
        if self._thread:
            self._thread.join(timeout)

    def _thread_main(self, task_id, text, limits, agents) -> None:
        asyncio.run(self._main(task_id, text, limits, agents))

    async def _main(self, task_id, text, limits, agents) -> None:
        self._loop = asyncio.get_running_loop()
        self._stop_event = asyncio.Event()
        if self._stop_flag.is_set():
            self._stop_event.set()
        gateway = Gateway(self.usage, self.adapter_factory, min_interval=limits.min_interval)
        council = Council(agents, gateway, self.store, self.usage, self.state, limits, self._stop_event)
        try:
            await council.run(task_id, text)
        except Exception as e:  # never crash the thread silently; keep the saved history
            msg = f"Unexpected error: {type(e).__name__}: {str(e)[:200]}"
            self.state.update(status="failed", error=msg, final_markdown=f"**The council stopped because of an error.** {msg}")
            self.store.finish_task(task_id, "failed", msg, False, msg)
