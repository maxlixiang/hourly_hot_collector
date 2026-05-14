# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Any


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def reflect_source_summary_results(items: list[dict[str, Any]]) -> list[str]:
    """Return compact self-check notes for objective source summary answers."""

    notes: list[str] = []
    total_sources = 0
    missing_urls = 0
    unknown_times = 0
    reader_success = 0
    reader_failed = 0
    fallback_summaries = 0

    for item in items:
        for source in item.get("sources", []) or []:
            total_sources += 1
            if not normalize_text(source.get("url")):
                missing_urls += 1
            if normalize_text(source.get("published_time_beijing")) in {"", "未知"}:
                unknown_times += 1

            reader = source.get("article_reader") or {}
            reader_status = normalize_text(reader.get("status"))
            if reader_status == "success":
                reader_success += 1
            elif reader_status in {"failed", "skipped"}:
                reader_failed += 1

            if source.get("summary_source") != "article_reader":
                fallback_summaries += 1

    if total_sources == 0:
        notes.append("未找到可展开的来源文章，本轮无法做来源级整理。")
        return notes

    if reader_success == 0:
        notes.append("本轮未成功读取原文，回答已回退到数据库摘要/标题。")
    elif reader_failed:
        notes.append(f"共有 {reader_success}/{total_sources} 个来源成功读取原文，其余来源已使用本地摘要兜底。")

    if fallback_summaries:
        notes.append(f"共有 {fallback_summaries} 个来源使用了数据库摘要或标题兜底，未加入专家判断。")

    if missing_urls:
        notes.append(f"共有 {missing_urls} 个来源缺少 URL，无法进行原文读取。")

    if unknown_times:
        notes.append(f"共有 {unknown_times} 个来源缺少可解析发布时间。")

    return notes


def reflect_expert_matches(matches: list[dict[str, Any]]) -> list[str]:
    notes: list[str] = []
    if not matches:
        notes.append("未命中已有专家报告，回答应保持客观来源整理口径。")
        return notes

    missing_knowledge = [
        item for item in matches if not item.get("expert_context_used")
    ]
    if missing_knowledge:
        notes.append("部分专家分析未命中明确知识卡片，应避免把推断写成事实。")
    return notes
