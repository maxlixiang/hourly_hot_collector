# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from zoneinfo import ZoneInfo


TIMEZONE = "Asia/Shanghai"
DB_FILE = "data/db/data_hub.db"
HOT_DIR = "data/hot"
LEGACY_HOT_DIR = "output_hot"
CONTEXT_OUTPUT_DIR = "data/analysis/context"
SUPPORTED_SOURCE_TYPES = ("newsnow", "rss")
HOT_FILE_PREFIX_MAP = {
    "newsnow": "newsnow_hot_clusters_*.json",
    "rss": "rss_hot_clusters_*.json",
}


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def parse_datetime_object(value: str | None) -> datetime | None:
    text = normalize_text(value)
    if not text:
        return None
    try:
        parsed = datetime.strptime(text, "%Y-%m-%d %H:%M:%S %z")
    except ValueError:
        return None
    return parsed.astimezone(ZoneInfo(TIMEZONE))


def resolve_article_time(article: dict[str, Any]) -> datetime | None:
    published_at = parse_datetime_object(article.get("published_at"))
    if published_at is not None:
        return published_at
    return parse_datetime_object(article.get("fetched_at"))


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def get_connection(db_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    return connection


def find_latest_hot_clusters_file(base_dir: Path, source_type: str) -> Path:
    pattern = HOT_FILE_PREFIX_MAP[source_type]
    candidates = sorted((base_dir / HOT_DIR / source_type).glob(pattern))
    if candidates:
        return candidates[-1]

    legacy_candidates = sorted((base_dir / LEGACY_HOT_DIR).glob(pattern))
    if legacy_candidates:
        return legacy_candidates[-1]

    raise FileNotFoundError(f"No hot cluster file found for source_type={source_type}")


def load_hot_clusters(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def extract_article_ids(cluster: dict[str, Any]) -> list[int]:
    article_ids = [int(article_id) for article_id in cluster.get("article_ids", [])]
    if article_ids:
        return article_ids

    fallback_ids: list[int] = []
    for article in cluster.get("articles", []):
        article_id = article.get("id")
        if article_id is None:
            continue
        fallback_ids.append(int(article_id))
    return fallback_ids


def fetch_articles_by_ids(db_path: Path, article_ids: list[int]) -> list[dict[str, Any]]:
    if not article_ids:
        return []

    placeholders = ",".join("?" for _ in article_ids)
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
            normalized_title,
            language
        FROM news_items
        WHERE id IN ({placeholders})
    """

    with get_connection(db_path) as connection:
        rows = connection.execute(query, article_ids).fetchall()

    row_map = {int(row["id"]): dict(row) for row in rows}
    articles: list[dict[str, Any]] = []
    for article_id in article_ids:
        article = row_map.get(int(article_id))
        if article is None:
            print(f"[WARN] Article id not found in SQLite: {article_id}")
            continue
        article["id"] = int(article["id"])
        article["source_type"] = normalize_text(article.get("source_type"))
        article["source_id"] = normalize_text(article.get("source_id")) or None
        article["source_name"] = normalize_text(article.get("source_name"))
        article["title"] = normalize_text(article.get("title"))
        article["url"] = normalize_text(article.get("url")) or None
        article["summary"] = normalize_text(article.get("summary")) or None
        article["published_at"] = normalize_text(article.get("published_at")) or None
        article["fetched_at"] = normalize_text(article.get("fetched_at")) or None
        article["normalized_title"] = normalize_text(article.get("normalized_title")) or None
        article["language"] = normalize_text(article.get("language")) or None
        resolved_time = resolve_article_time(article)
        article["resolved_time"] = (
            resolved_time.strftime("%Y-%m-%d %H:%M:%S %z") if resolved_time is not None else None
        )
        articles.append(article)
    return articles


def build_timeline(articles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    articles_sorted = sorted(
        articles,
        key=lambda article: resolve_article_time(article) or datetime.min.replace(tzinfo=ZoneInfo(TIMEZONE)),
    )
    return [
        {
            "time": article.get("resolved_time") or article.get("published_at") or article.get("fetched_at"),
            "title": article["title"],
            "source_name": article["source_name"],
        }
        for article in articles_sorted
    ]


def choose_event_title(cluster: dict[str, Any], articles: list[dict[str, Any]]) -> str:
    representative_titles = cluster.get("representative_titles") or []
    if representative_titles:
        return normalize_text(representative_titles[0])

    if articles:
        longest_article = max(articles, key=lambda article: len(normalize_text(article.get("title"))))
        return normalize_text(longest_article.get("title"))

    return normalize_text(cluster.get("cluster_id")) or "untitled_event"


def build_context_stats(articles: list[dict[str, Any]]) -> dict[str, Any]:
    article_times = [resolve_article_time(article) for article in articles]
    valid_times = [article_time for article_time in article_times if article_time is not None]
    if not valid_times:
        return {
            "article_count": len(articles),
            "source_count": len({normalize_text(article["source_name"]) for article in articles}),
            "time_span_hours": 0.0,
            "earliest_time": None,
            "latest_time": None,
        }

    earliest_time = min(valid_times)
    latest_time = max(valid_times)
    time_span_hours = round((latest_time - earliest_time).total_seconds() / 3600, 4)
    return {
        "article_count": len(articles),
        "source_count": len({normalize_text(article["source_name"]) for article in articles}),
        "time_span_hours": time_span_hours,
        "earliest_time": earliest_time.strftime("%Y-%m-%d %H:%M:%S %z"),
        "latest_time": latest_time.strftime("%Y-%m-%d %H:%M:%S %z"),
    }


def build_contexts_for_source_type(
    base_dir: Path,
    source_type: str,
    input_file: Path | None = None,
) -> Path:
    db_path = base_dir / DB_FILE
    if not db_path.exists():
        raise FileNotFoundError(f"SQLite database not found: {db_path}")

    hot_clusters_file = input_file or find_latest_hot_clusters_file(base_dir, source_type)
    payload = load_hot_clusters(hot_clusters_file)
    contexts: list[dict[str, Any]] = []

    for cluster in payload.get("clusters", []):
        article_ids = extract_article_ids(cluster)
        articles = fetch_articles_by_ids(db_path, article_ids)
        articles_sorted_desc = sorted(
            articles,
            key=lambda article: resolve_article_time(article) or datetime.min.replace(tzinfo=ZoneInfo(TIMEZONE)),
            reverse=True,
        )
        contexts.append(
            {
                "cluster_id": cluster.get("cluster_id"),
                "rank": cluster.get("rank"),
                "heat_score": cluster.get("heat_score"),
                "total_articles": cluster.get("total_articles"),
                "unique_sources": cluster.get("unique_sources"),
                "event_title": choose_event_title(cluster, articles_sorted_desc),
                "representative_titles": cluster.get("representative_titles", []),
                "sources": cluster.get("sources", []),
                "article_ids": article_ids,
                "articles": articles_sorted_desc,
                "timeline": build_timeline(articles_sorted_desc),
                "context_stats": build_context_stats(articles_sorted_desc),
            }
        )

    output_dir = base_dir / CONTEXT_OUTPUT_DIR
    output_dir.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now(ZoneInfo(TIMEZONE))
    output_path = output_dir / f"{source_type}_cluster_context_{generated_at.strftime('%Y-%m-%d_%H')}.json"
    output_payload = {
        "generated_at": generated_at.strftime("%Y-%m-%d %H:%M:%S %z"),
        "source_type": source_type,
        "source_hot_clusters_file": str(hot_clusters_file),
        "cluster_count": len(contexts),
        "contexts": contexts,
    }
    output_path.write_text(json.dumps(output_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build cluster context JSON from hot cluster outputs.")
    parser.add_argument(
        "--source-type",
        choices=SUPPORTED_SOURCE_TYPES,
        help="Only build context for the specified source type.",
    )
    parser.add_argument(
        "--input-file",
        help="Use a specific hot cluster JSON file instead of auto-detecting the latest one.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    base_dir = project_root()
    input_file = Path(args.input_file).resolve() if args.input_file else None
    if args.source_type and input_file is not None:
        payload = load_hot_clusters(input_file)
        inferred_source_type = normalize_text(payload.get("source_type"))
        if inferred_source_type != args.source_type:
            raise ValueError(
                f"--source-type {args.source_type} does not match input file source_type {inferred_source_type}"
            )
        source_types = [args.source_type]
    elif args.source_type:
        source_types = [args.source_type]
    elif input_file is not None:
        payload = load_hot_clusters(input_file)
        inferred_source_type = normalize_text(payload.get("source_type"))
        if inferred_source_type not in SUPPORTED_SOURCE_TYPES:
            raise ValueError("Unable to infer source_type from input hot cluster file.")
        source_types = [inferred_source_type]
    else:
        source_types = list(SUPPORTED_SOURCE_TYPES)

    for source_type in source_types:
        source_input_file = input_file if input_file is not None else None
        output_path = build_contexts_for_source_type(base_dir, source_type, source_input_file)
        print(f"[INFO] Cluster context output: {output_path}")


if __name__ == "__main__":
    main()
