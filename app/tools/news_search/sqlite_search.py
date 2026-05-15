# -*- coding: utf-8 -*-
from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from app.tools.news_search.query_parser import normalize_text, parse_query_keywords
from app.tools.news_search.schemas import NewsSearchItem, SourceType


TIMEZONE = "Asia/Shanghai"
DB_FILE = "data/db/data_hub.db"
SUPPORTED_SOURCE_TYPES = {"newsnow", "rss", "mixed"}


def now_dt() -> datetime:
    return datetime.now(ZoneInfo(TIMEZONE))


def parse_dt(value: Any) -> datetime | None:
    text = normalize_text(value)
    if not text:
        return None
    normalized = text
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=ZoneInfo(TIMEZONE))
        return parsed.astimezone(ZoneInfo(TIMEZONE))
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S %z", "%Y-%m-%d %H:%M:%S"):
        try:
            parsed = datetime.strptime(text, fmt)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=ZoneInfo(TIMEZONE))
            return parsed.astimezone(ZoneInfo(TIMEZONE))
        except ValueError:
            continue
    return None


def resolve_source_type(source_type: str) -> SourceType:
    normalized = normalize_text(source_type).lower() or "mixed"
    if normalized not in SUPPORTED_SOURCE_TYPES:
        return "mixed"
    return normalized  # type: ignore[return-value]


def keyword_matches_text(keyword: str, text: str) -> bool:
    normalized_keyword = normalize_text(keyword).lower()
    normalized_text = normalize_text(text).lower()
    if not normalized_keyword or not normalized_text:
        return False
    if re.search(r"[a-z]", normalized_keyword):
        pattern = rf"(?<![a-z0-9]){re.escape(normalized_keyword)}(?![a-z0-9])"
        return re.search(pattern, normalized_text, flags=re.IGNORECASE) is not None
    return normalized_keyword in normalized_text


def row_event_time(row: dict[str, Any]) -> datetime | None:
    return parse_dt(row.get("published_at")) or parse_dt(row.get("fetched_at"))


def connect_readonly(db_path: Path) -> sqlite3.Connection:
    db_uri = db_path.resolve().as_posix()
    connection = sqlite3.connect(f"file:{db_uri}?mode=ro&immutable=1", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def build_sql(source_type: SourceType, keyword_count: int) -> tuple[str, list[Any]]:
    clauses = ["(LOWER(title) LIKE ? OR LOWER(COALESCE(summary, '')) LIKE ?)"] * keyword_count
    source_filter = ""
    params: list[Any] = []
    if source_type != "mixed":
        source_filter = "AND source_type = ?"
        params.append(source_type)

    query = f"""
        SELECT
            id,
            source_type,
            source_id,
            source_name,
            title,
            url,
            summary,
            published_at,
            fetched_at,
            language
        FROM news_items
        WHERE (
            (published_at IS NOT NULL AND TRIM(published_at) != '' AND published_at >= ?)
            OR ((published_at IS NULL OR TRIM(published_at) = '') AND fetched_at >= ?)
        )
          {source_filter}
          AND ({' OR '.join(clauses)})
        ORDER BY COALESCE(NULLIF(published_at, ''), fetched_at) DESC, id DESC
        LIMIT ?
    """
    return query, params


def score_row(row: dict[str, Any], keywords: list[str]) -> tuple[int, int, list[str]]:
    title = normalize_text(row.get("title"))
    summary = normalize_text(row.get("summary"))
    matched: list[str] = []
    title_hits = 0
    summary_hits = 0
    for keyword in keywords:
        title_match = keyword_matches_text(keyword, title)
        summary_match = keyword_matches_text(keyword, summary)
        if title_match or summary_match:
            matched.append(keyword)
        if title_match:
            title_hits += 1
        if summary_match:
            summary_hits += 1
    return title_hits, summary_hits, matched


def item_from_row(row: dict[str, Any], keywords: list[str]) -> NewsSearchItem | None:
    title_hits, summary_hits, matched = score_row(row, keywords)
    if not matched:
        return None
    return NewsSearchItem(
        id=int(row.get("id") or 0),
        source_type=normalize_text(row.get("source_type")),
        source_id=normalize_text(row.get("source_id")),
        source_name=normalize_text(row.get("source_name")),
        title=normalize_text(row.get("title")),
        url=normalize_text(row.get("url")),
        summary=normalize_text(row.get("summary")),
        published_at=normalize_text(row.get("published_at")),
        fetched_at=normalize_text(row.get("fetched_at")),
        language=normalize_text(row.get("language")),
        matched_keywords=matched,
        title_hit_count=title_hits,
        summary_hit_count=summary_hits,
    )


def build_result_dedup_key(item: NewsSearchItem) -> str:
    if item.url:
        return f"{item.source_type}:url:{item.url}"
    title = normalize_text(item.title).lower()
    return f"{item.source_type}:title:{title}"


def search_news(
    base_dir: str | Path,
    query: str,
    window_hours: int = 24,
    source_type: str = "mixed",
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Search recent news_items by topic, time window, and source_type."""
    base = Path(base_dir)
    db_path = base / DB_FILE
    keywords = parse_query_keywords(query)
    if not db_path.exists() or not keywords:
        return []

    normalized_source_type = resolve_source_type(source_type)
    normalized_window = max(1, int(window_hours or 24))
    normalized_limit = max(1, int(limit or 20))
    cutoff = now_dt() - timedelta(hours=normalized_window)
    cutoff_text = cutoff.strftime("%Y-%m-%d %H:%M:%S %z")

    sql, source_params = build_sql(normalized_source_type, min(len(keywords), 12))
    keyword_params = [f"%{keyword.lower()}%" for keyword in keywords[:12]]
    params: list[Any] = [cutoff_text, cutoff_text, *source_params]
    for keyword_param in keyword_params:
        params.extend([keyword_param, keyword_param])
    params.append(max(normalized_limit * 5, normalized_limit))

    connection = connect_readonly(db_path)
    try:
        rows = [dict(row) for row in connection.execute(sql, params).fetchall()]
    finally:
        connection.close()

    items: list[tuple[datetime, NewsSearchItem]] = []
    for row in rows:
        event_time = row_event_time(row)
        if event_time is None or event_time < cutoff:
            continue
        item = item_from_row(row, keywords)
        if item is not None:
            items.append((event_time, item))

    items.sort(
        key=lambda pair: (
            -pair[1].title_hit_count,
            -pair[1].summary_hit_count,
            -pair[0].timestamp(),
            -pair[1].id,
        )
    )

    deduped_items: list[NewsSearchItem] = []
    seen_keys: set[str] = set()
    for _, item in items:
        dedup_key = build_result_dedup_key(item)
        if dedup_key in seen_keys:
            continue
        seen_keys.add(dedup_key)
        deduped_items.append(item)
        if len(deduped_items) >= normalized_limit:
            break

    return [item.to_dict() for item in deduped_items]
