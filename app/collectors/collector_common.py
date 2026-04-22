# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import json
import re
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class Source:
    name: str
    source_id: str


@dataclass(frozen=True)
class RssSource:
    name: str
    url: str


BASE_URL = os.getenv("NEWSNOW_BASE_URL", "https://www.cnnewsnow.com")
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "./data/markdown/newsnow"))
RSS_OUTPUT_DIR = Path(os.getenv("RSS_OUTPUT_DIR", "./data/markdown/rss"))
NEWSNOW_RAW_DIR = Path(os.getenv("NEWSNOW_RAW_DIR", "./data/raw/newsnow"))
RSS_RAW_DIR = Path(os.getenv("RSS_RAW_DIR", "./data/raw/rss"))
RSS_DATABASE_FILE = Path(os.getenv("RSS_DATABASE_FILE", "./config/rss_sources.txt"))
DB_FILE = Path(os.getenv("DB_FILE", "./data/db/data_hub.db"))
TIMEZONE = os.getenv("TIMEZONE", "Asia/Shanghai")
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "20"))
RUN_MINUTE = int(os.getenv("RUN_MINUTE", "0"))
USER_AGENT = os.getenv(
    "USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
)
FAILED_LOG_FILE = Path(os.getenv("FAILED_LOG_FILE", "./logs/failed_sources.log"))

SOURCES: list[Source] = [
    Source("微博热搜", "weibo"),
    Source("知乎热榜", "zhihu"),
    Source("今日头条", "toutiao"),
    Source("抖音", "douyin"),
    Source("百度热搜", "baidu"),
    Source("百度贴吧 热议", "tieba"),
    Source("哔哩哔哩热搜", "bilibili-hot-search"),
    Source("腾讯新闻", "tencent-hot"),
    Source("36Kr", "36kr"),
    Source("华尔街见闻 最热", "wallstreetcn-hot"),
    Source("财联社", "cls-hot"),
    Source("澎湃新闻 热榜", "thepaper"),
    Source("卫星通讯社", "sputniknewscn"),
    Source("参考消息", "cankaoxiaoxi"),
    Source("快手", "kuaishou"),
]

COMMON_HEADERS = {
    "User-Agent": USER_AGENT,
    "Referer": f"{BASE_URL}/",
    "Accept": "application/json, text/plain, */*",
}

RSS_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "application/rss+xml, application/xml, text/xml, application/atom+xml, text/plain, */*",
}


def now_local() -> datetime:
    return datetime.now(ZoneInfo(TIMEZONE))


def now_text() -> str:
    return now_local().strftime("%Y-%m-%d %H:%M:%S %z")


def format_dt(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S %z")


def hourly_stamp(dt: datetime | None = None) -> str:
    current = dt or now_local()
    return current.strftime("%Y-%m-%d_%H")


def next_run_time(current: datetime) -> datetime:
    candidate = current.replace(minute=RUN_MINUTE, second=0, microsecond=0)
    if candidate <= current:
        candidate += timedelta(hours=1)
    return candidate


def fallback_window_start(current: datetime) -> datetime:
    return current - timedelta(hours=1)


def sleep_until_next_run() -> None:
    current = now_local()
    target = next_run_time(current)
    seconds = max(1, int((target - current).total_seconds()))
    print(f"[INFO] Current time: {format_dt(current)}")
    print(f"[INFO] Next run: {format_dt(target)}")
    print(f"[INFO] Sleeping for {seconds} seconds")
    time.sleep(seconds)


def ensure_output_dirs() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    RSS_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    NEWSNOW_RAW_DIR.mkdir(parents=True, exist_ok=True)
    RSS_RAW_DIR.mkdir(parents=True, exist_ok=True)
    DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    FAILED_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)


def append_failed_log(channel: str, name: str, identity: str, error: Exception) -> None:
    ensure_output_dirs()
    line = f"[{now_text()}] [{channel}] {name} ({identity}): {type(error).__name__}: {error}\n"
    with open(FAILED_LOG_FILE, "a", encoding="utf-8") as file:
        file.write(line)


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def normalize_title(value: str) -> str:
    lowered = unicodedata.normalize("NFKC", normalize_text(value)).lower()
    lowered = re.sub(r"\s+", " ", lowered)
    lowered = re.sub(r"[^\w\s]", " ", lowered)
    lowered = re.sub(r"_", " ", lowered)
    return re.sub(r"\s+", " ", lowered).strip()


def slugify_text(value: str) -> str:
    normalized = normalize_title(value)
    slug = re.sub(r"\s+", "-", normalized)
    return slug[:200]


def source_slug_from_url(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.netloc.replace("www.", "")
    path = parsed.path.strip("/").replace("/", "-")
    base = f"{host}-{path}".strip("-")
    return slugify_text(base or url) or "rss-source"


def parse_datetime_value(value: Any) -> datetime | None:
    text = normalize_text(value)
    if not text:
        return None

    try:
        parsed = datetime.strptime(text, "%Y-%m-%d %H:%M:%S %z")
    except ValueError:
        try:
            parsed = parsedate_to_datetime(text)
        except (TypeError, ValueError, IndexError, OverflowError):
            return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo(TIMEZONE))
    return parsed.astimezone(ZoneInfo(TIMEZONE))


def parse_possible_datetime(value: Any) -> str | None:
    text = normalize_text(value)
    if not text:
        return None
    parsed = parse_datetime_value(text)
    if parsed is None:
        return text
    return format_dt(parsed)


def parse_app_datetime(value: str | None) -> datetime | None:
    text = normalize_text(value)
    if not text:
        return None
    try:
        parsed = datetime.strptime(text, "%Y-%m-%d %H:%M:%S %z")
    except ValueError:
        return None
    return parsed.astimezone(ZoneInfo(TIMEZONE))


def first_non_empty(data: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = normalize_text(data.get(key))
        if value:
            return value
    return None


def extract_newsnow_published_at(item: dict[str, Any]) -> str | None:
    candidate = first_non_empty(
        item,
        "published_at",
        "publish_time",
        "pubDate",
        "pub_date",
        "time",
        "datetime",
        "date",
    )
    return parse_possible_datetime(candidate)


def filter_rss_items_for_window(
    items: list[dict[str, Any]],
    window_start: datetime,
    current_run_at: datetime,
) -> list[dict[str, Any]]:
    filtered_items: list[dict[str, Any]] = []

    for item in items:
        published_dt = parse_datetime_value(item.get("pubDate"))
        if published_dt is None:
            continue
        if window_start < published_dt <= current_run_at:
            filtered_items.append(item)

    filtered_items.sort(
        key=lambda entry: parse_datetime_value(entry.get("pubDate")) or window_start,
        reverse=True,
    )
    return filtered_items


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def newsnow_output_file_path(generated_at: datetime) -> Path:
    return OUTPUT_DIR / f"daily_hot_{hourly_stamp(generated_at)}.md"


def rss_output_file_path(generated_at: datetime) -> Path:
    return RSS_OUTPUT_DIR / f"rss_hot_{hourly_stamp(generated_at)}.md"


def newsnow_raw_file_path(generated_at: datetime) -> Path:
    return NEWSNOW_RAW_DIR / f"newsnow_{hourly_stamp(generated_at)}.json"


def rss_raw_file_path(generated_at: datetime) -> Path:
    return RSS_RAW_DIR / f"rss_{hourly_stamp(generated_at)}.json"


def summarize_run_status(
    newsnow_failed: list[str],
    rss_failed: list[str],
    rss_source_count: int,
) -> str:
    newsnow_all_failed = len(newsnow_failed) == len(SOURCES)
    rss_all_failed = rss_source_count > 0 and len(rss_failed) == rss_source_count

    if not newsnow_failed and not rss_failed:
        return "success"
    if newsnow_all_failed and rss_all_failed:
        return "failed"
    return "partial"


def build_run_note(newsnow_failed: list[str], rss_failed: list[str]) -> str | None:
    note_parts: list[str] = []
    if newsnow_failed:
        note_parts.append("NewsNow failed: " + " | ".join(newsnow_failed))
    if rss_failed:
        note_parts.append("RSS failed: " + " | ".join(rss_failed))
    if not note_parts:
        return None
    return "\n".join(note_parts)
