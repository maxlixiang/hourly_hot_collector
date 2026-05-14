# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


SUPPORTED_TASK_TYPES = (
    "hot_news_query",
    "source_summary_request",
    "expert_topic_analysis",
)

SUPPORTED_AGENT_SOURCE_TYPES = ("newsnow", "rss", "mixed")


@dataclass(slots=True)
class AgentPlan:
    task_type: str
    user_query: str
    params: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    planner_notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class AgentResponse:
    task_type: str
    answer_text: str
    data: dict[str, Any] = field(default_factory=dict)
    output_file: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
