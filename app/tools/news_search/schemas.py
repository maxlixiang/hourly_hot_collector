# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal


SourceType = Literal["newsnow", "rss", "mixed"]


@dataclass(frozen=True)
class NewsSearchItem:
    id: int
    source_type: str
    source_id: str
    source_name: str
    title: str
    url: str
    summary: str
    published_at: str
    fetched_at: str
    language: str
    matched_keywords: list[str]
    title_hit_count: int
    summary_hit_count: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class NewsSearchQuery:
    query: str
    keywords: list[str]
    window_hours: int
    source_type: SourceType
    limit: int
