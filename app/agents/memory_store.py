# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from zoneinfo import ZoneInfo


TIMEZONE = "Asia/Shanghai"
SESSION_STATE_FILE = "data/agent/session_state.json"
RUNTIME_FALLBACK_DIR = ".agent_runtime"
MAX_INTERACTION_HISTORY = 50


def now_text() -> str:
    return datetime.now(ZoneInfo(TIMEZONE)).strftime("%Y-%m-%d %H:%M:%S %z")


def truncate_text(value: Any, limit: int = 220) -> str:
    text = "" if value is None else str(value).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


class MemoryStore:
    """Lightweight file-backed memory for the local agent v1."""

    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir
        self.primary_path = base_dir / SESSION_STATE_FILE
        self.fallback_path = base_dir / RUNTIME_FALLBACK_DIR / "session_state.json"

    def _read_path(self, path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"[WARN] Unable to read memory file {path}: {exc}")
            return {}

    def load(self) -> dict[str, Any]:
        primary = self._read_path(self.primary_path)
        if primary:
            return primary
        return self._read_path(self.fallback_path)

    def _write_path(self, path: Path, payload: dict[str, Any]) -> Path | None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as exc:
            print(f"[WARN] Unable to write memory file {path}: {exc}")
            return None
        return path

    def save(self, payload: dict[str, Any], *, merge: bool = True) -> Path | None:
        state = self.load() if merge else {}
        history = state.get("interaction_history", [])
        state.update(payload)
        if history and "interaction_history" not in payload:
            state["interaction_history"] = history
        state["memory_updated_at"] = now_text()

        written = self._write_path(self.primary_path, state)
        if written is not None:
            return written
        return self._write_path(self.fallback_path, state)

    def append_interaction(
        self,
        *,
        task_type: str,
        query: str,
        answer: str,
        output_file: str | None = None,
    ) -> Path | None:
        state = self.load()
        history = state.get("interaction_history", [])
        if not isinstance(history, list):
            history = []

        history.append(
            {
                "timestamp": now_text(),
                "task_type": task_type,
                "query": truncate_text(query, 260),
                "answer_preview": truncate_text(answer, 360),
                "output_file": output_file,
            }
        )
        state["interaction_history"] = history[-MAX_INTERACTION_HISTORY:]
        state["last_task_type"] = task_type
        state["last_query"] = query
        return self.save(state, merge=False)
