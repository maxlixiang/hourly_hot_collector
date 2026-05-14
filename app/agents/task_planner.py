# -*- coding: utf-8 -*-
from __future__ import annotations

import re
from typing import Any

from app.agents.agent_schemas import AgentPlan, SUPPORTED_AGENT_SOURCE_TYPES, SUPPORTED_TASK_TYPES


DEFAULT_HOT_LIMIT = 10
DEFAULT_WINDOW_HOURS = 24

CHINESE_NUMBER_MAP = {
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}

SOURCE_TYPE_ALIASES = {
    "newsnow": ("newsnow", "热榜", "中文热榜", "中文"),
    "rss": ("rss", "外媒", "英文", "正式新闻", "新闻源"),
}


def normalize_query(text: str) -> str:
    return (
        text.strip()
        .replace("，", ",")
        .replace("、", ",")
        .replace("；", ";")
        .replace("：", ":")
        .replace("　", " ")
    )


def parse_int_token(token: str) -> int | None:
    token = token.strip()
    if not token:
        return None
    if token.isdigit():
        return int(token)
    return CHINESE_NUMBER_MAP.get(token)


def extract_time_window_hours(query: str) -> int:
    normalized = normalize_query(query)
    lowered = normalized.lower()

    match = re.search(r"(\d+)\s*(?:小时|小時|h|hour|hours)", lowered)
    if match:
        return max(1, int(match.group(1)))

    match = re.search(r"([一二两三四五六七八九十])\s*(?:小时|小時)", normalized)
    if match:
        parsed = parse_int_token(match.group(1))
        if parsed:
            return parsed

    match = re.search(r"(\d+)\s*(?:天|日|day|days)", lowered)
    if match:
        return max(1, int(match.group(1))) * 24

    match = re.search(r"([一二两三四五六七八九十])\s*(?:天|日)", normalized)
    if match:
        parsed = parse_int_token(match.group(1))
        if parsed:
            return parsed * 24

    if "一周" in normalized or "1周" in normalized or "一星期" in normalized:
        return 24 * 7

    return DEFAULT_WINDOW_HOURS


def extract_limit(query: str) -> int:
    normalized = normalize_query(query)
    match = re.search(r"(\d+)\s*(?:条|个|则|篇)", normalized)
    if match:
        return max(1, min(50, int(match.group(1))))

    match = re.search(r"([一二两三四五六七八九十])\s*(?:条|个|则|篇)", normalized)
    if match:
        parsed = parse_int_token(match.group(1))
        if parsed:
            return max(1, min(50, parsed))

    return DEFAULT_HOT_LIMIT


def extract_selected_indexes(query: str) -> list[int]:
    normalized = normalize_query(query)
    indexes = [int(token) for token in re.findall(r"\d+", normalized)]
    seen: set[int] = set()
    result: list[int] = []
    for index in indexes:
        if index <= 0 or index in seen:
            continue
        seen.add(index)
        result.append(index)
    return result


def infer_source_type(query: str) -> str:
    lowered = normalize_query(query).lower()
    for source_type, aliases in SOURCE_TYPE_ALIASES.items():
        for alias in aliases:
            if alias.lower() in lowered:
                return source_type
    return "mixed"


def infer_task_type(query: str, explicit_task_type: str | None = None) -> tuple[str, float, list[str]]:
    if explicit_task_type:
        if explicit_task_type not in SUPPORTED_TASK_TYPES:
            raise ValueError(f"Unsupported task_type: {explicit_task_type}")
        return explicit_task_type, 1.0, ["task_type was provided explicitly"]

    normalized = normalize_query(query)
    lowered = normalized.lower()
    selected_indexes = extract_selected_indexes(normalized)
    notes: list[str] = []

    source_summary_cues = ("总结", "整理", "主要观点", "来源", "原文", "内容")
    expert_cues = ("专家", "预测", "影响", "分析一下", "从专家", "怎么看", "判断")
    hot_cues = ("热点", "热门", "过去", "最近", "新闻")

    if selected_indexes and any(cue in normalized for cue in source_summary_cues):
        notes.append("detected selected item indexes with source-summary cues")
        return "source_summary_request", 0.86, notes

    if any(cue in normalized for cue in expert_cues):
        notes.append("detected expert-analysis cues")
        return "expert_topic_analysis", 0.78, notes

    if any(cue in normalized for cue in hot_cues) or "hot" in lowered:
        notes.append("detected hot-news cues")
        return "hot_news_query", 0.75, notes

    notes.append("fallback to hot_news_query")
    return "hot_news_query", 0.45, notes


def extract_topic_text(query: str) -> str:
    normalized = normalize_query(query)
    cleaned = normalized
    for phrase in (
        "从专家的角度",
        "从专家角度",
        "帮我",
        "请",
        "分析一下",
        "分析",
        "过去",
        "最近",
        "新闻",
        "热点",
        "做一个预测",
        "预测",
        "造成的影响",
        "影响",
    ):
        cleaned = cleaned.replace(phrase, " ")
    cleaned = re.sub(r"\d+\s*(?:小时|天|日|周|条|个|则|篇)", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,;:")
    return cleaned or normalized


def plan_user_request(
    query: str,
    explicit_task_type: str | None = None,
    overrides: dict[str, Any] | None = None,
) -> AgentPlan:
    normalized_query = normalize_query(query)
    task_type, confidence, notes = infer_task_type(normalized_query, explicit_task_type)
    params: dict[str, Any] = {
        "window_hours": extract_time_window_hours(normalized_query),
        "source_type": infer_source_type(normalized_query),
    }

    if task_type == "hot_news_query":
        params["limit"] = extract_limit(normalized_query)
    elif task_type == "source_summary_request":
        params["selected_indexes"] = extract_selected_indexes(normalized_query)
    elif task_type == "expert_topic_analysis":
        params["topic"] = extract_topic_text(normalized_query)
        params["limit"] = 5

    if overrides:
        for key, value in overrides.items():
            if value is not None:
                params[key] = value

    source_type = params.get("source_type")
    if source_type not in SUPPORTED_AGENT_SOURCE_TYPES:
        params["source_type"] = "mixed"

    return AgentPlan(
        task_type=task_type,
        user_query=normalized_query,
        params=params,
        confidence=confidence,
        planner_notes=notes,
    )
