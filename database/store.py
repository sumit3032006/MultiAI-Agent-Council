"""SQLite storage for tasks, messages and API usage."""
from __future__ import annotations

import sqlite3
import threading
import uuid
from contextlib import closing
from pathlib import Path
from typing import Optional

from engine.messages import Message, now_iso

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks(
  id TEXT PRIMARY KEY, text TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL,
  finished_at TEXT, final_answer TEXT, provisional INTEGER DEFAULT 0, stop_reason TEXT);
CREATE TABLE IF NOT EXISTS messages(
  id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, round INTEGER NOT NULL,
  sender TEXT NOT NULL, receiver TEXT NOT NULL, type TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS usage(
  id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, provider TEXT NOT NULL, model TEXT NOT NULL, ok INTEGER NOT NULL,
  input_tokens INTEGER, output_tokens INTEGER, latency REAL, error_kind TEXT, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_messages_task ON messages(task_id);
CREATE INDEX IF NOT EXISTS idx_usage_task ON usage(task_id);
"""


class Store:
    def __init__(self, path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._keep = sqlite3.connect(self.path, check_same_thread=False) if self.path == ":memory:" else None
        conn = self._conn()
        try:
            conn.executescript(SCHEMA)
            conn.commit()
        finally:
            if not self._keep:
                conn.close()   # `with conn:` only commits; on Windows an unclosed file cannot be deleted

    def _conn(self):
        return self._keep if self._keep else sqlite3.connect(self.path, timeout=10)

    def _run(self, sql: str, args=(), fetch: bool = False):
        with self._lock:
            conn = self._conn()
            try:
                cur = conn.execute(sql, args)
                rows = [dict(zip([d[0] for d in cur.description], r)) for r in cur.fetchall()] if fetch else None
                conn.commit()
                return rows
            finally:
                if not self._keep:
                    conn.close()

    # ---- tasks ----------------------------------------------------------
    def create_task(self, text: str) -> str:
        task_id = uuid.uuid4().hex[:12]
        self._run("INSERT INTO tasks(id,text,status,created_at) VALUES(?,?,?,?)", (task_id, text, "running", now_iso()))
        return task_id

    def finish_task(self, task_id: str, status: str, final_answer: str = "", provisional: bool = False,
                    stop_reason: str = "") -> None:
        self._run("UPDATE tasks SET status=?, finished_at=?, final_answer=?, provisional=?, stop_reason=? WHERE id=?",
                  (status, now_iso(), final_answer, int(provisional), stop_reason, task_id))

    def list_tasks(self) -> list[dict]:
        return self._run("SELECT * FROM tasks ORDER BY created_at DESC, rowid DESC", fetch=True)

    def get_task(self, task_id: str) -> Optional[dict]:
        rows = self._run("SELECT * FROM tasks WHERE id=?", (task_id,), fetch=True)
        return rows[0] if rows else None

    def delete_tasks(self, ids: list[str]) -> int:
        n = 0
        for task_id in ids:
            for table in ("messages", "usage", "tasks"):
                col = "id" if table == "tasks" else "task_id"
                self._run(f"DELETE FROM {table} WHERE {col}=?", (task_id,))
            n += 1
        return n

    def clear_all(self) -> None:
        for table in ("messages", "usage", "tasks"):
            self._run(f"DELETE FROM {table}")

    # ---- messages -------------------------------------------------------
    def add_message(self, m: Message) -> None:
        self._run("INSERT INTO messages(task_id,round,sender,receiver,type,content,created_at) VALUES(?,?,?,?,?,?,?)",
                  (m.task_id, m.round, m.sender, m.receiver, m.type.value, m.content, m.timestamp))

    def get_messages(self, task_id: str) -> list[dict]:
        return self._run("SELECT task_id,round,sender,receiver,type,content,created_at AS timestamp "
                         "FROM messages WHERE task_id=? ORDER BY id", (task_id,), fetch=True)

    def get_proposals(self, task_id: str) -> list[dict]:
        """Initial proposals and revisions, in order."""
        return [m for m in self.get_messages(task_id) if m["type"] in ("PROPOSAL", "REVISION")]

    # ---- usage ----------------------------------------------------------
    def add_usage(self, rec: dict) -> None:
        self._run("INSERT INTO usage(task_id,provider,model,ok,input_tokens,output_tokens,latency,error_kind,created_at) "
                  "VALUES(?,?,?,?,?,?,?,?,?)",
                  (rec.get("task_id"), rec["provider"], rec["model"], int(rec["ok"]), rec.get("input_tokens"),
                   rec.get("output_tokens"), rec.get("latency"), rec.get("error_kind"), now_iso()))

    def usage_for_task(self, task_id: str) -> list[dict]:
        return self._run("SELECT provider,model,COUNT(*) AS requests, SUM(1-ok) AS failures, "
                         "SUM(COALESCE(input_tokens,0)) AS input_tokens, SUM(COALESCE(output_tokens,0)) AS output_tokens "
                         "FROM usage WHERE task_id=? GROUP BY provider,model", (task_id,), fetch=True)
