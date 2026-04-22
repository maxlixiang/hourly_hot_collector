# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import requests
from requests.exceptions import ReadTimeout, Timeout

from app.collectors.collector_common import (
    BASE_URL,
    COMMON_HEADERS,
    DB_FILE,
    REQUEST_TIMEOUT,
    SOURCES,
    Source,
    append_failed_log,
    extract_newsnow_published_at,
    format_dt,
    newsnow_output_file_path,
    newsnow_raw_file_path,
    normalize_text,
    normalize_title,
    write_json,
)
from app.storage.db import bulk_insert_news_items


def _do_newsnow_request(source: Source) -> dict[str, Any]:
    url = f"{BASE_URL}/api/s?id={source.source_id}"
    response = requests.get(url, headers=COMMON_HEADERS, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise ValueError(f"Unexpected response format: {source.source_id}")
    return data


def fetch_newsnow_source(source: Source) -> dict[str, Any]:
    try:
        return _do_newsnow_request(source)
    except (ReadTimeout, Timeout) as exc:
        print(f"[WARN] [NEWSNOW] {source.name} ({source.source_id}) timed out, retrying once")
        try:
            return _do_newsnow_request(source)
        except Exception as retry_exc:
            append_failed_log("NEWSNOW", source.name, source.source_id, retry_exc)
            raise retry_exc from exc
    except Exception as exc:
        append_failed_log("NEWSNOW", source.name, source.source_id, exc)
        raise


def normalize_newsnow_items(data: dict[str, Any]) -> list[dict[str, Any]]:
    items = data.get("items", [])
    return items if isinstance(items, list) else []


def render_newsnow_section(source: Source, data: dict[str, Any]) -> str:
    lines: list[str] = [
        f"## {source.name}",
        "",
        f"> Source ID: `{source.source_id}`",
        "",
    ]

    items = normalize_newsnow_items(data)
    if not items:
        lines.append("暂无数据")
        lines.append("")
        return "\n".join(lines)

    for index, item in enumerate(items, start=1):
        title = normalize_text(item.get("title")) or "无标题"
        url = normalize_text(item.get("url"))
        if url:
            lines.append(f"{index}. [{title}]({url})")
        else:
            lines.append(f"{index}. {title}")

    lines.append("")
    return "\n".join(lines)


def build_newsnow_markdown(
    results: list[tuple[Source, dict[str, Any]]],
    failed: list[str],
    generated_at: datetime,
) -> str:
    lines: list[str] = [
        "# 今日热点日报",
        "",
        f"> 生成时间: {format_dt(generated_at)}",
        "",
        f"> 数据来源: NewsNow API ({BASE_URL})",
        "",
    ]

    if failed:
        lines.extend(["## 抓取异常", ""])
        for message in failed:
            lines.append(f"- {message}")
        lines.append("")

    for source, data in results:
        lines.append(render_newsnow_section(source, data))

    return "\n".join(lines)


def standardize_newsnow_item(
    source: Source,
    item: dict[str, Any],
    fetch_run_id: int,
    fetched_at: str,
    raw_file_path: Path,
    raw_source_index: int | None,
    raw_item_index: int,
) -> dict[str, Any]:
    title = normalize_text(item.get("title")) or "无标题"
    url = normalize_text(item.get("url"))
    return {
        "fetch_run_id": fetch_run_id,
        "source_type": "newsnow",
        "source_id": source.source_id,
        "source_name": source.name,
        "title": title,
        "url": url,
        "summary": None,
        "published_at": extract_newsnow_published_at(item),
        "fetched_at": fetched_at,
        "normalized_title": normalize_title(title),
        "content": None,
        "content_fetch_status": "pending",
        "content_fetch_error": None,
        "raw_file_path": str(raw_file_path),
        "raw_source_index": raw_source_index,
        "raw_item_index": raw_item_index,
        "category_hint": None,
        "language": "zh",
        "created_at": fetched_at,
    }


def collect_newsnow(generated_at: datetime, fetch_run_id: int) -> dict[str, Any]:
    fetched_at = format_dt(generated_at)
    markdown_path = newsnow_output_file_path(generated_at)
    raw_path = newsnow_raw_file_path(generated_at)

    results: list[tuple[Source, dict[str, Any]]] = []
    failed: list[str] = []
    raw_results: list[dict[str, Any]] = []
    standardized_items: list[dict[str, Any]] = []

    print(f"[INFO] [NEWSNOW] Start collecting {len(SOURCES)} sources")
    for source_index, source in enumerate(SOURCES):
        try:
            data = fetch_newsnow_source(source)
            items = normalize_newsnow_items(data)

            results.append((source, data))
            raw_results.append(
                {
                    "source_name": source.name,
                    "source_id": source.source_id,
                    "fetched_at": fetched_at,
                    "status": "ok",
                    "data": data,
                }
            )

            for item_index, item in enumerate(items):
                standardized_items.append(
                    standardize_newsnow_item(
                        source=source,
                        item=item,
                        fetch_run_id=fetch_run_id,
                        fetched_at=fetched_at,
                        raw_file_path=raw_path,
                        raw_source_index=source_index,
                        raw_item_index=item_index,
                    )
                )

            print(f"[OK] [NEWSNOW] {source.name} ({source.source_id})")
        except Exception as exc:
            failed_message = f"{source.name} ({source.source_id}): {exc}"
            failed.append(failed_message)
            results.append((source, {"items": []}))
            raw_results.append(
                {
                    "source_name": source.name,
                    "source_id": source.source_id,
                    "fetched_at": fetched_at,
                    "status": "failed",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            print(f"[ERROR] [NEWSNOW] {failed_message}")

    markdown_content = build_newsnow_markdown(results, failed, generated_at)
    markdown_path.write_text(markdown_content, encoding="utf-8")
    write_json(
        raw_path,
        {
            "generated_at": fetched_at,
            "source": "NewsNow",
            "base_url": BASE_URL,
            "results": raw_results,
        },
    )

    inserted_count = bulk_insert_news_items(standardized_items, DB_FILE)

    print(f"[INFO] [NEWSNOW] Markdown saved to: {markdown_path}")
    print(f"[INFO] [NEWSNOW] Raw data saved to: {raw_path}")
    print(f"[INFO] [NEWSNOW] Inserted into SQLite: {inserted_count}")
    return {
        "markdown_path": markdown_path,
        "raw_path": raw_path,
        "failed": failed,
        "item_count": inserted_count,
    }
