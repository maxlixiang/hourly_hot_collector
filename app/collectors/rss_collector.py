# -*- coding: utf-8 -*-
from __future__ import annotations

import ast
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

from app.collectors.collector_common import (
    DB_FILE,
    REQUEST_TIMEOUT,
    RSS_DATABASE_FILE,
    RSS_HEADERS,
    RssSource,
    append_failed_log,
    fallback_window_start,
    filter_rss_items_for_window,
    format_dt,
    normalize_text,
    normalize_title,
    parse_app_datetime,
    parse_datetime_value,
    parse_possible_datetime,
    rss_output_file_path,
    rss_raw_file_path,
    source_slug_from_url,
    write_json,
)
from app.storage.db import bulk_insert_news_items, get_previous_successful_run_finished_at


def load_rss_sources() -> list[RssSource]:
    if not RSS_DATABASE_FILE.exists():
        raise FileNotFoundError(f"RSS source config file not found: {RSS_DATABASE_FILE}")

    source_text = RSS_DATABASE_FILE.read_text(encoding="utf-8")
    module = ast.parse(source_text, filename=str(RSS_DATABASE_FILE))

    for node in module.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "RSS_SOURCES":
                    value = ast.literal_eval(node.value)
                    if not isinstance(value, list):
                        raise ValueError("RSS_SOURCES must be a list")

                    rss_sources: list[RssSource] = []
                    for item in value:
                        if not (isinstance(item, tuple) and len(item) == 2):
                            raise ValueError("Each RSS source must be a (name, url) tuple")
                        name, url = item
                        if not isinstance(name, str) or not isinstance(url, str):
                            raise ValueError("RSS source name/url must be strings")
                        rss_sources.append(RssSource(name=name.strip(), url=url.strip()))
                    return rss_sources

    raise ValueError("RSS_SOURCES = [...] was not found in the RSS source file")


def element_text(parent: ET.Element, *names: str) -> str:
    for child in list(parent):
        local_name = child.tag.split("}", 1)[-1]
        if local_name in names:
            return "".join(child.itertext()).strip()
    return ""


def parse_rss_datetime(value: str) -> str:
    return parse_possible_datetime(value) or ""


def parse_rss_entries(xml_text: str) -> list[dict[str, str]]:
    root = ET.fromstring(xml_text)
    tag_name = root.tag.split("}", 1)[-1].lower()
    entries: list[dict[str, str]] = []

    if tag_name == "feed":
        for entry in root.findall(".//{*}entry"):
            title = element_text(entry, "title") or "无标题"
            link = ""
            for link_node in entry.findall("{*}link"):
                href = normalize_text(link_node.attrib.get("href"))
                rel = normalize_text(link_node.attrib.get("rel")) or "alternate"
                if href and rel in {"alternate", ""}:
                    link = href
                    break
                if href and not link:
                    link = href
            pub_date = parse_rss_datetime(
                element_text(entry, "published") or element_text(entry, "updated")
            )
            description = element_text(entry, "summary", "content")
            entries.append(
                {
                    "title": title,
                    "link": link,
                    "pubDate": pub_date,
                    "description": description,
                }
            )
        return entries

    item_nodes = root.findall(".//item")
    if not item_nodes:
        item_nodes = root.findall(".//{*}item")

    for item in item_nodes:
        title = element_text(item, "title") or "无标题"
        link = element_text(item, "link")
        pub_date = parse_rss_datetime(
            element_text(item, "pubDate", "date", "published", "updated")
        )
        description = element_text(item, "description", "encoded", "summary")
        entries.append(
            {
                "title": title,
                "link": link,
                "pubDate": pub_date,
                "description": description,
            }
        )

    return entries


def fetch_rss_source(
    source: RssSource,
    fetched_at: str,
    window_start: datetime,
    current_run_at: datetime,
) -> dict[str, Any]:
    try:
        response = requests.get(source.url, headers=RSS_HEADERS, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        parsed_items = parse_rss_entries(response.text)
        filtered_items = filter_rss_items_for_window(parsed_items, window_start, current_run_at)
        return {
            "source_name": source.name,
            "source_url": source.url,
            "fetched_at": fetched_at,
            "status": "ok",
            "items": [
                {
                    "title": entry["title"],
                    "link": entry["link"],
                    "pubDate": entry["pubDate"],
                    "description": entry["description"],
                    "source_name": source.name,
                    "fetched_at": fetched_at,
                }
                for entry in filtered_items
            ],
        }
    except Exception as exc:
        append_failed_log("RSS", source.name, source.url, exc)
        raise


def build_rss_markdown(results: list[dict[str, Any]], generated_at: datetime) -> str:
    lines: list[str] = [
        "# RSS 本轮增量更新",
        "",
        f"> 生成时间: {format_dt(generated_at)}",
        "> 数据来源: RSS",
        "",
    ]

    visible_results = [
        result for result in results if result.get("status") != "ok" or result.get("items")
    ]
    if not visible_results:
        lines.append("本轮没有符合窗口条件的 RSS 新消息。")
        lines.append("")
        return "\n".join(lines)

    for result in visible_results:
        source_name = normalize_text(result.get("source_name")) or "未知来源"
        lines.append(f"## {source_name}")
        lines.append("")

        if result.get("status") != "ok":
            lines.append("该源抓取失败")
            lines.append("")
            continue

        for index, item in enumerate(result.get("items", []), start=1):
            title = normalize_text(item.get("title")) or "无标题"
            link = normalize_text(item.get("link"))
            pub_date = normalize_text(item.get("pubDate"))
            line = f"{index}. [{title}]({link})" if link else f"{index}. {title}"
            if pub_date:
                line += f" ({pub_date})"
            lines.append(line)

        lines.append("")

    return "\n".join(lines)


def standardize_rss_item(
    source: RssSource,
    item: dict[str, Any],
    fetch_run_id: int,
    fetched_at: str,
    raw_file_path: Path,
    raw_source_index: int,
    raw_item_index: int,
) -> dict[str, Any]:
    title = normalize_text(item.get("title")) or "无标题"
    url = normalize_text(item.get("link"))
    return {
        "fetch_run_id": fetch_run_id,
        "source_type": "rss",
        "source_id": source_slug_from_url(source.url),
        "source_name": source.name,
        "title": title,
        "url": url,
        "summary": normalize_text(item.get("description")) or None,
        "published_at": parse_possible_datetime(item.get("pubDate")),
        "fetched_at": fetched_at,
        "normalized_title": normalize_title(title),
        "content": None,
        "content_fetch_status": "pending",
        "content_fetch_error": None,
        "raw_file_path": str(raw_file_path),
        "raw_source_index": raw_source_index,
        "raw_item_index": raw_item_index,
        "category_hint": None,
        "language": "en",
        "created_at": fetched_at,
    }


def collect_rss(generated_at: datetime, fetch_run_id: int) -> dict[str, Any]:
    rss_sources = load_rss_sources()
    fetched_at = format_dt(generated_at)
    previous_finished_at = get_previous_successful_run_finished_at(DB_FILE, fetch_run_id)
    window_start = parse_app_datetime(previous_finished_at) or fallback_window_start(generated_at)
    markdown_path = rss_output_file_path(generated_at)
    raw_path = rss_raw_file_path(generated_at)

    results: list[dict[str, Any]] = []
    failed: list[str] = []
    standardized_items: list[dict[str, Any]] = []
    success_count = 0

    print(f"[INFO] [RSS] Start collecting {len(rss_sources)} sources")
    for source_index, source in enumerate(rss_sources):
        try:
            result = fetch_rss_source(source, fetched_at, window_start, generated_at)
            results.append(result)
            success_count += 1

            for item_index, item in enumerate(result.get("items", [])):
                standardized_items.append(
                    standardize_rss_item(
                        source=source,
                        item=item,
                        fetch_run_id=fetch_run_id,
                        fetched_at=fetched_at,
                        raw_file_path=raw_path,
                        raw_source_index=source_index,
                        raw_item_index=item_index,
                    )
                )

            print(f"[OK] [RSS] {source.name} ({source.url})")
        except Exception as exc:
            failed_message = f"{source.name} ({source.url}): {exc}"
            failed.append(failed_message)
            results.append(
                {
                    "source_name": source.name,
                    "source_url": source.url,
                    "fetched_at": fetched_at,
                    "status": "failed",
                    "items": [],
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            print(f"[ERROR] [RSS] {failed_message}")

    markdown_content = build_rss_markdown(results, generated_at)
    markdown_path.write_text(markdown_content, encoding="utf-8")
    write_json(
        raw_path,
        {
            "generated_at": fetched_at,
            "window_start": format_dt(window_start),
            "window_end": fetched_at,
            "source": "RSS",
            "rss_database_file": str(RSS_DATABASE_FILE),
            "results": results,
        },
    )

    inserted_count = bulk_insert_news_items(standardized_items, DB_FILE)

    print(f"[INFO] [RSS] Markdown saved to: {markdown_path}")
    print(f"[INFO] [RSS] Raw data saved to: {raw_path}")
    print(f"[INFO] [RSS] Inserted into SQLite: {inserted_count}")
    return {
        "markdown_path": markdown_path,
        "raw_path": raw_path,
        "failed": failed,
        "source_count": len(rss_sources),
        "success_count": success_count,
        "failed_count": len(failed),
        "item_count": inserted_count,
    }
