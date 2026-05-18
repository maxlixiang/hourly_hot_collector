# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import json
import ssl
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

import requests
from requests.exceptions import SSLError

from app.collectors.collector_common import (
    RSS_HEADERS,
    TIMEZONE,
    format_dt,
    now_local,
    parse_datetime_value,
)
from app.collectors.rss_collector import load_rss_sources, parse_rss_entries


DEFAULT_STALE_DAYS = 3
DEFAULT_MAX_WORKERS = 8
OUTPUT_DIR = Path("data/analysis/rss_health")
FETCH_HEADERS = {
    **RSS_HEADERS,
    "Accept-Encoding": "identity",
    "Connection": "close",
}


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def resolve_entry_time(entry: dict[str, Any]) -> datetime | None:
    return parse_datetime_value(
        entry.get("pubDate")
        or entry.get("published")
        or entry.get("updated")
        or entry.get("date")
    )


def fetch_feed_text(url: str, timeout: int) -> tuple[str, str]:
    request_error: Exception | None = None
    for verify in (True, False):
        try:
            response = requests.get(
                url,
                headers=FETCH_HEADERS,
                timeout=timeout,
                verify=verify,
            )
            response.raise_for_status()
            return response.text, "requests" if verify else "requests_no_verify"
        except SSLError as exc:
            request_error = exc
            continue
        except Exception:
            raise

    context = ssl._create_unverified_context()
    request = Request(url, headers=FETCH_HEADERS)
    try:
        with urlopen(request, timeout=timeout, context=context) as response:
            body = response.read()
            charset = response.headers.get_content_charset() or "utf-8"
            return body.decode(charset, errors="replace"), "urllib_no_verify"
    except URLError as exc:
        if request_error is not None:
            raise request_error from exc
        raise


def check_one_source(source: Any, stale_after: datetime, timeout: int) -> dict[str, Any]:
    checked_at = now_local()
    base_payload = {
        "source_name": source.name,
        "source_url": source.url,
        "checked_at": format_dt(checked_at),
    }

    try:
        feed_text, fetch_method = fetch_feed_text(source.url, timeout)
    except Exception as exc:
        return {
            **base_payload,
            "status": "failed",
            "item_count": 0,
            "latest_item_time": None,
            "age_hours": None,
            "fetch_method": None,
            "message": f"fetch failed: {type(exc).__name__}: {exc}",
        }

    try:
        entries = parse_rss_entries(feed_text)
    except Exception as exc:
        return {
            **base_payload,
            "status": "failed",
            "item_count": 0,
            "latest_item_time": None,
            "age_hours": None,
            "fetch_method": fetch_method,
            "message": f"parse failed after successful fetch: {type(exc).__name__}: {exc}",
        }

    dated_times = [
        entry_time
        for entry_time in (resolve_entry_time(entry) for entry in entries)
        if entry_time is not None
    ]

    if not dated_times:
        return {
            **base_payload,
            "status": "no_dated_items",
            "item_count": len(entries),
            "latest_item_time": None,
            "age_hours": None,
            "fetch_method": fetch_method,
            "message": "Feed fetched successfully, but no item time could be parsed.",
        }

    latest_time = max(dated_times)
    age_hours = max(0.0, (checked_at - latest_time).total_seconds() / 3600)
    is_stale = latest_time < stale_after
    return {
        **base_payload,
        "status": "stale" if is_stale else "ok",
        "item_count": len(entries),
        "dated_item_count": len(dated_times),
        "latest_item_time": format_dt(latest_time),
        "age_hours": round(age_hours, 2),
        "fetch_method": fetch_method,
        "message": (
            "Latest RSS item is older than the stale threshold."
            if is_stale
            else "Latest RSS item is within the stale threshold."
        ),
    }


def summarize_results(results: list[dict[str, Any]]) -> dict[str, int]:
    summary = {"ok": 0, "stale": 0, "failed": 0, "no_dated_items": 0}
    for result in results:
        status = normalize_text(result.get("status"))
        if status not in summary:
            continue
        summary[status] += 1
    summary["total"] = len(results)
    return summary


def render_markdown(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    lines = [
        "# RSS Health Check",
        "",
        f"> Checked at: {payload['checked_at']}",
        f"> Stale threshold: {payload['stale_days']} days",
        "",
        (
            f"Total: {summary['total']} | OK: {summary['ok']} | "
            f"Stale: {summary['stale']} | Failed: {summary['failed']} | "
            f"No dated items: {summary['no_dated_items']}"
        ),
        "",
    ]

    attention_items = [
        result
        for result in payload["results"]
        if result.get("status") in {"stale", "failed", "no_dated_items"}
    ]
    if not attention_items:
        lines.append("All RSS sources are healthy.")
        lines.append("")
        return "\n".join(lines)

    lines.append("## Needs Attention")
    lines.append("")
    for result in attention_items:
        latest = result.get("latest_item_time") or "unknown"
        age = result.get("age_hours")
        age_text = f"{age}h" if age is not None else "unknown"
        lines.append(f"- [{result['status']}] {result['source_name']}")
        lines.append(f"  - URL: {result['source_url']}")
        lines.append(f"  - Latest item: {latest} (age: {age_text})")
        lines.append(f"  - Message: {result['message']}")
    lines.append("")
    return "\n".join(lines)


def output_paths(output_dir: Path, checked_at: datetime) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = checked_at.strftime("%Y-%m-%d_%H%M%S")
    return (
        output_dir / f"rss_health_{stamp}.json",
        output_dir / f"rss_health_{stamp}.md",
    )


def check_rss_health(
    stale_days: int = DEFAULT_STALE_DAYS,
    timeout: int = 20,
    max_workers: int = DEFAULT_MAX_WORKERS,
    output_dir: Path = OUTPUT_DIR,
) -> dict[str, Any]:
    checked_at = now_local()
    stale_after = checked_at - timedelta(days=max(1, int(stale_days)))
    sources = load_rss_sources()

    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, int(max_workers))) as executor:
        future_map = {
            executor.submit(check_one_source, source, stale_after, timeout): source
            for source in sources
        }
        for future in as_completed(future_map):
            results.append(future.result())

    results.sort(key=lambda item: (normalize_text(item.get("status")), normalize_text(item.get("source_name"))))
    payload = {
        "checked_at": format_dt(checked_at),
        "timezone": TIMEZONE,
        "stale_days": max(1, int(stale_days)),
        "stale_after": format_dt(stale_after),
        "summary": summarize_results(results),
        "results": results,
    }

    json_path, markdown_path = output_paths(output_dir, checked_at)
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(render_markdown(payload), encoding="utf-8")
    payload["json_path"] = str(json_path)
    payload["markdown_path"] = str(markdown_path)
    return payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check RSS source freshness and fetch health.")
    parser.add_argument("--stale-days", type=int, default=DEFAULT_STALE_DAYS)
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--max-workers", type=int, default=DEFAULT_MAX_WORKERS)
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    payload = check_rss_health(
        stale_days=args.stale_days,
        timeout=args.timeout,
        max_workers=args.max_workers,
        output_dir=Path(args.output_dir),
    )
    summary = payload["summary"]
    print(
        "[INFO] RSS health: "
        f"total={summary['total']} ok={summary['ok']} stale={summary['stale']} "
        f"failed={summary['failed']} no_dated_items={summary['no_dated_items']}"
    )
    print(f"[INFO] RSS health JSON: {payload['json_path']}")
    print(f"[INFO] RSS health Markdown: {payload['markdown_path']}")


if __name__ == "__main__":
    main()
