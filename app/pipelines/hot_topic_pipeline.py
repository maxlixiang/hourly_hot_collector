# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import json
import math
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
from sentence_transformers import SentenceTransformer
from sklearn.cluster import AgglomerativeClustering
from zoneinfo import ZoneInfo


TIMEZONE = "Asia/Shanghai"
DB_FILE = "data/db/data_hub.db"
DEFAULT_ANALYSIS_WINDOW_HOURS = 6
COLLECTION_INTERVAL_HOURS = 1
MODEL_NAME = "sentence-transformers/paraphrase-multilingual-mpnet-base-v2"
DECAY_LAMBDA = 0.08
TOP_N_CLUSTERS = 10
OUTPUT_DIR = "data/hot"
OUTPUT_FILE_PREFIX_MAP = {
    "newsnow": "newsnow_hot_clusters",
    "rss": "rss_hot_clusters",
}
REPRESENTATIVE_TITLES_PER_CLUSTER = 3
SUPPORTED_SOURCE_TYPES = ("newsnow", "rss")
NEWSNOW_FREQUENCY_WORDS_FILE = "config/newsnow_frequency_words.txt"
NEWSNOW_EVENT_RULES_FILE = "config/newsnow_event_rules.txt"
NEWSNOW_EVENT_SCORE_THRESHOLD = 3

PIPELINE_CONFIG = {
    "newsnow": {
        "similarity_threshold": 0.46,
        "min_title_length": 8,
        "enable_noise_filter": True,
        "enable_event_score": True,
    },
    "rss": {
        "similarity_threshold": 0.32,
        "min_title_length": 0,
        "enable_noise_filter": False,
        "enable_event_score": False,
    },
}

_EMBEDDING_MODEL: SentenceTransformer | None = None
_NEWSNOW_FREQUENCY_WORDS_CACHE: dict[str, set[str]] | None = None
_NEWSNOW_FREQUENCY_WORDS_MTIME: float | None = None
_NEWSNOW_EVENT_RULES_CACHE: dict[str, set[str]] | None = None
_NEWSNOW_EVENT_RULES_MTIME: float | None = None


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


def get_connection(db_path: Path) -> sqlite3.Connection:
    db_uri = db_path.resolve().as_posix()
    connection = sqlite3.connect(f"file:{db_uri}?mode=ro&immutable=1", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def resolve_article_time(article: dict[str, Any]) -> datetime | None:
    resolved_at = parse_datetime_object(article.get("resolved_time"))
    if resolved_at is not None:
        return resolved_at
    published_at = parse_datetime_object(article.get("published_at"))
    if published_at is not None:
        return published_at
    return parse_datetime_object(article.get("fetched_at"))


def normalize_window_hours(window_hours: int | str | None) -> int:
    try:
        parsed = int(window_hours or DEFAULT_ANALYSIS_WINDOW_HOURS)
    except (TypeError, ValueError):
        parsed = DEFAULT_ANALYSIS_WINDOW_HOURS
    return max(1, min(24 * 30, parsed))


def floor_to_hour(value: datetime) -> datetime:
    return value.replace(minute=0, second=0, microsecond=0)


def resolve_complete_hour_window(now_dt: datetime, window_hours: int) -> tuple[datetime, datetime]:
    end_dt = floor_to_hour(now_dt)
    start_dt = end_dt - timedelta(hours=window_hours)
    return start_dt, end_dt


def has_observations_table(connection: sqlite3.Connection) -> bool:
    row = connection.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table'
          AND name = 'news_item_observations'
        LIMIT 1
        """
    ).fetchone()
    return row is not None


def load_articles_from_sqlite(
    base_dir: Path,
    source_type: str,
    analysis_window_hours: int = DEFAULT_ANALYSIS_WINDOW_HOURS,
    now_dt: datetime | None = None,
) -> list[dict[str, Any]]:
    db_path = base_dir / DB_FILE
    if not db_path.exists():
        raise FileNotFoundError(f"SQLite database not found: {db_path}")

    if source_type not in SUPPORTED_SOURCE_TYPES:
        raise ValueError(f"Unsupported source_type: {source_type}")

    resolved_now_dt = now_dt or datetime.now(ZoneInfo(TIMEZONE))
    window_hours = normalize_window_hours(analysis_window_hours)
    window_start_dt, window_end_dt = resolve_complete_hour_window(resolved_now_dt, window_hours)
    window_start_text = window_start_dt.strftime("%Y-%m-%d %H:%M:%S %z")
    window_end_text = window_end_dt.strftime("%Y-%m-%d %H:%M:%S %z")

    with get_connection(db_path) as connection:
        observations_available = has_observations_table(connection)
        if observations_available:
            query = """
                SELECT
                    n.id,
                    n.source_type,
                    n.source_id,
                    o.source_name AS source_name,
                    n.title,
                    n.url,
                    n.summary,
                    COALESCE(o.published_at, n.published_at) AS published_at,
                    o.fetched_at AS fetched_at,
                    n.normalized_title,
                    n.language,
                    o.raw_item_index AS observation_rank,
                    o.fetched_at AS observation_fetched_at
                FROM news_item_observations o
                JOIN news_items n ON n.id = o.news_item_id
                WHERE o.source_type = ?
                  AND n.title IS NOT NULL
                  AND TRIM(n.title) != ''
                  AND o.fetched_at >= ?
                  AND o.fetched_at < ?

                UNION ALL

                SELECT
                    n.id,
                    n.source_type,
                    n.source_id,
                    n.source_name,
                    n.title,
                    n.url,
                    n.summary,
                    n.published_at,
                    n.fetched_at,
                    n.normalized_title,
                    n.language,
                    n.raw_item_index AS observation_rank,
                    n.fetched_at AS observation_fetched_at
                FROM news_items n
                WHERE n.source_type = ?
                  AND n.title IS NOT NULL
                  AND TRIM(n.title) != ''
                  AND NOT EXISTS (
                      SELECT 1
                      FROM news_item_observations o
                      WHERE o.news_item_id = n.id
                  )
                  AND (
                        (
                            n.published_at IS NOT NULL
                            AND TRIM(n.published_at) != ''
                            AND n.published_at >= ?
                            AND n.published_at < ?
                        )
                     OR (
                            (n.published_at IS NULL OR TRIM(n.published_at) = '')
                            AND n.fetched_at IS NOT NULL
                            AND TRIM(n.fetched_at) != ''
                            AND n.fetched_at >= ?
                            AND n.fetched_at < ?
                        )
                  )
                ORDER BY fetched_at DESC, id DESC
            """
            rows = connection.execute(
                query,
                (
                    source_type,
                    window_start_text,
                    window_end_text,
                    source_type,
                    window_start_text,
                    window_end_text,
                    window_start_text,
                    window_end_text,
                ),
            ).fetchall()
        else:
            query = """
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
                    language,
                    raw_item_index AS observation_rank,
                    fetched_at AS observation_fetched_at
                FROM news_items
                WHERE source_type = ?
                  AND title IS NOT NULL
                  AND TRIM(title) != ''
                  AND (
                        (
                            published_at IS NOT NULL
                            AND TRIM(published_at) != ''
                            AND published_at >= ?
                            AND published_at < ?
                        )
                     OR (
                            (published_at IS NULL OR TRIM(published_at) = '')
                            AND fetched_at IS NOT NULL
                            AND TRIM(fetched_at) != ''
                            AND fetched_at >= ?
                            AND fetched_at < ?
                        )
                  )
                ORDER BY COALESCE(published_at, fetched_at) DESC, id DESC
            """
            rows = connection.execute(
                query,
                (source_type, window_start_text, window_end_text, window_start_text, window_end_text),
            ).fetchall()

    observation_rows: list[dict[str, Any]] = []
    for row in rows:
        article = dict(row)
        article["title"] = normalize_text(article.get("title"))
        article["url"] = normalize_text(article.get("url")) or None
        article["summary"] = normalize_text(article.get("summary")) or None
        article["published_at"] = normalize_text(article.get("published_at")) or None
        article["fetched_at"] = normalize_text(article.get("fetched_at")) or None
        article["normalized_title"] = normalize_text(article.get("normalized_title")) or None
        article["source_name"] = normalize_text(article.get("source_name"))
        article["source_type"] = normalize_text(article.get("source_type"))
        article["source_id"] = normalize_text(article.get("source_id")) or None
        article["language"] = normalize_text(article.get("language")) or None

        observation_fetched_at = parse_datetime_object(article.get("observation_fetched_at"))
        resolved_time = observation_fetched_at or resolve_article_time(article)
        if resolved_time is None or resolved_time < window_start_dt or resolved_time >= window_end_dt:
            continue
        article["resolved_time"] = resolved_time.strftime("%Y-%m-%d %H:%M:%S %z")
        article["observation_time"] = (
            observation_fetched_at.strftime("%Y-%m-%d %H:%M:%S %z")
            if observation_fetched_at is not None
            else article["resolved_time"]
        )
        observation_rows.append(article)

    return aggregate_observed_articles(observation_rows)


def safe_int(value: Any) -> int | None:
    try:
        if value is None or str(value).strip() == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def aggregate_observed_articles(observation_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in observation_rows:
        grouped[build_dedup_key(row)].append(row)

    articles: list[dict[str, Any]] = []
    for rows in grouped.values():
        preferred = rows[0]
        for row in rows[1:]:
            preferred = choose_preferred_article(preferred, row)

        observation_times = [
            parse_datetime_object(row.get("observation_time")) or resolve_article_time(row)
            for row in rows
        ]
        valid_times = [value for value in observation_times if value is not None]
        if valid_times:
            first_seen_dt = min(valid_times)
            last_seen_dt = max(valid_times)
            active_hours = max(0.0, (last_seen_dt - first_seen_dt).total_seconds() / 3600)
        else:
            first_seen_dt = None
            last_seen_dt = None
            active_hours = 0.0

        latest_rows = [
            row
            for row in rows
            if (parse_datetime_object(row.get("observation_time")) or resolve_article_time(row)) == last_seen_dt
        ]
        latest_ranks = [
            rank
            for rank in (safe_int(row.get("observation_rank")) for row in latest_rows)
            if rank is not None
        ]

        article = dict(preferred)
        article["seen_count"] = len(rows)
        article["first_seen"] = first_seen_dt.strftime("%Y-%m-%d %H:%M:%S %z") if first_seen_dt else None
        article["last_seen"] = last_seen_dt.strftime("%Y-%m-%d %H:%M:%S %z") if last_seen_dt else None
        article["active_hours"] = round(active_hours, 2)
        article["unique_sources"] = len({normalize_text(row.get("source_name")) for row in rows if normalize_text(row.get("source_name"))})
        article["source_count"] = article["unique_sources"]
        article["latest_rank"] = min(latest_ranks) + 1 if latest_ranks else None
        articles.append(article)

    articles.sort(
        key=lambda article: resolve_article_time(article) or datetime.min.replace(tzinfo=ZoneInfo(TIMEZONE)),
        reverse=True,
    )
    return articles


def build_dedup_key(article: dict[str, Any]) -> str:
    url = normalize_text(article.get("url"))
    normalized_title = normalize_text(article.get("normalized_title"))
    title = normalize_text(article.get("title"))

    if url:
        return f"url::{url}"
    if normalized_title:
        return f"normalized_title::{normalized_title}"
    return f"title::{title}"


def choose_preferred_article(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    left_time = resolve_article_time(left)
    right_time = resolve_article_time(right)

    if left_time is None and right_time is None:
        return right if int(right["id"]) > int(left["id"]) else left
    if left_time is None:
        return right
    if right_time is None:
        return left
    if right_time > left_time:
        return right
    if left_time > right_time:
        return left
    return right if int(right["id"]) > int(left["id"]) else left


def deduplicate_articles(articles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped_by_key: dict[str, dict[str, Any]] = {}

    for article in articles:
        dedup_key = build_dedup_key(article)
        existing = deduped_by_key.get(dedup_key)
        if existing is None:
            deduped_by_key[dedup_key] = article
        else:
            deduped_by_key[dedup_key] = choose_preferred_article(existing, article)

    deduped_articles = list(deduped_by_key.values())
    deduped_articles.sort(
        key=lambda article: resolve_article_time(article) or datetime.min.replace(tzinfo=ZoneInfo(TIMEZONE)),
        reverse=True,
    )
    return deduped_articles


def get_source_config(source_type: str) -> dict[str, Any]:
    return PIPELINE_CONFIG[source_type]


def load_sectioned_word_file(config_path: Path, section_mapping: dict[str, str]) -> dict[str, set[str]]:
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    parsed: dict[str, set[str]] = {target_name: set() for target_name in section_mapping.values()}
    current_section: str | None = None

    for raw_line in config_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section_name = line[1:-1].strip().upper()
            if section_name not in section_mapping:
                raise ValueError(f"Unsupported section in {config_path}: {section_name}")
            current_section = section_mapping[section_name]
            continue
        if current_section is None:
            raise ValueError(f"Value found before section declaration in {config_path}: {line}")
        parsed[current_section].add(line)

    return parsed


def load_newsnow_frequency_words(base_dir: Path) -> dict[str, set[str]]:
    global _NEWSNOW_FREQUENCY_WORDS_CACHE
    global _NEWSNOW_FREQUENCY_WORDS_MTIME
    config_path = base_dir / NEWSNOW_FREQUENCY_WORDS_FILE
    if not config_path.exists():
        raise FileNotFoundError(f"NewsNow frequency words file not found: {config_path}")
    current_mtime = config_path.stat().st_mtime

    if (
        _NEWSNOW_FREQUENCY_WORDS_CACHE is not None
        and _NEWSNOW_FREQUENCY_WORDS_MTIME == current_mtime
    ):
        return _NEWSNOW_FREQUENCY_WORDS_CACHE

    parsed = load_sectioned_word_file(
        config_path,
        {
            "GLOBAL_FILTER": "global_filter",
            "WEAK_NEWS_PATTERNS": "weak_news_patterns",
        },
    )
    _NEWSNOW_FREQUENCY_WORDS_CACHE = parsed
    _NEWSNOW_FREQUENCY_WORDS_MTIME = current_mtime
    return parsed


def load_newsnow_event_rules(base_dir: Path) -> dict[str, set[str]]:
    global _NEWSNOW_EVENT_RULES_CACHE
    global _NEWSNOW_EVENT_RULES_MTIME
    config_path = base_dir / NEWSNOW_EVENT_RULES_FILE
    if not config_path.exists():
        raise FileNotFoundError(f"NewsNow event rules file not found: {config_path}")
    current_mtime = config_path.stat().st_mtime

    if (
        _NEWSNOW_EVENT_RULES_CACHE is not None
        and _NEWSNOW_EVENT_RULES_MTIME == current_mtime
    ):
        return _NEWSNOW_EVENT_RULES_CACHE

    parsed = load_sectioned_word_file(
        config_path,
        {
            "POSITIVE_EVENT_WORDS": "positive_event_words",
            "NEGATIVE_EVENT_WORDS": "negative_event_words",
            "POSITIVE_SOURCES": "positive_sources",
            "NEGATIVE_SOURCES": "negative_sources",
        },
    )
    _NEWSNOW_EVENT_RULES_CACHE = parsed
    _NEWSNOW_EVENT_RULES_MTIME = current_mtime
    return parsed


def title_hits_frequency_words(title: str, words: set[str]) -> bool:
    lowered = title.lower()
    for word in words:
        if word.lower() in lowered:
            return True
    return False


def find_matching_words(text: str, words: set[str]) -> list[str]:
    lowered = text.lower()
    matches = [word for word in words if word.lower() in lowered]
    return sorted(set(matches))


def apply_source_quality_filter(
    base_dir: Path,
    source_type: str,
    articles: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    config = get_source_config(source_type)
    min_title_length = int(config["min_title_length"])
    enable_noise_filter = bool(config["enable_noise_filter"])

    frequency_words: dict[str, set[str]] | None = None
    if source_type == "newsnow" and enable_noise_filter:
        frequency_words = load_newsnow_frequency_words(base_dir)

    filtered_articles: list[dict[str, Any]] = []
    for article in articles:
        title = normalize_text(article.get("title"))
        if len(title) < min_title_length:
            continue
        if source_type == "newsnow" and enable_noise_filter and frequency_words is not None:
            if title_hits_frequency_words(title, frequency_words["global_filter"]):
                continue
            if title_hits_frequency_words(title, frequency_words["weak_news_patterns"]):
                continue
        filtered_articles.append(article)
    return filtered_articles


def calculate_news_event_score(article: dict[str, Any], rules: dict[str, set[str]]) -> dict[str, Any]:
    title = normalize_text(article.get("title"))
    source_name = normalize_text(article.get("source_name"))
    positive_hits = find_matching_words(title, rules["positive_event_words"])
    negative_hits = find_matching_words(title, rules["negative_event_words"])
    positive_source_hits = find_matching_words(source_name, rules["positive_sources"])
    negative_source_hits = find_matching_words(source_name, rules["negative_sources"])

    score = 0
    matched_positive_rules: list[str] = []
    matched_negative_rules: list[str] = []

    if positive_hits:
        score += min(2, len(positive_hits))
        matched_positive_rules.extend(f"title:{word}" for word in positive_hits)
    if negative_hits:
        score -= min(2, len(negative_hits))
        matched_negative_rules.extend(f"title:{word}" for word in negative_hits)
    if positive_source_hits:
        score += 1
        matched_positive_rules.extend(f"source:{word}" for word in positive_source_hits)
    if negative_source_hits:
        score -= 1
        matched_negative_rules.extend(f"source:{word}" for word in negative_source_hits)

    title_length = len(title)
    if 12 <= title_length <= 36:
        score += 1
        matched_positive_rules.append("length:reasonable")
    elif title_length < 10:
        score -= 1
        matched_negative_rules.append("length:too_short")

    return {
        "score": score,
        "matched_positive_rules": matched_positive_rules,
        "matched_negative_rules": matched_negative_rules,
    }


def apply_newsnow_event_score_filter(
    base_dir: Path,
    source_type: str,
    articles: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    if source_type != "newsnow":
        return articles, 0

    config = get_source_config(source_type)
    if not bool(config.get("enable_event_score", False)):
        return articles, 0

    event_rules = load_newsnow_event_rules(base_dir)
    kept_articles: list[dict[str, Any]] = []
    filtered_count = 0

    for article in articles:
        score_result = calculate_news_event_score(article, event_rules)
        article["news_event_score"] = score_result["score"]
        if score_result["score"] >= NEWSNOW_EVENT_SCORE_THRESHOLD:
            kept_articles.append(article)
        else:
            filtered_count += 1

    return kept_articles, filtered_count


def get_embedding_model(model_name: str) -> SentenceTransformer:
    global _EMBEDDING_MODEL
    if _EMBEDDING_MODEL is None:
        _EMBEDDING_MODEL = SentenceTransformer(model_name)
    return _EMBEDDING_MODEL


def build_embeddings(titles: list[str], model_name: str) -> np.ndarray:
    model = get_embedding_model(model_name)
    embeddings = model.encode(
        titles,
        batch_size=32,
        normalize_embeddings=True,
        show_progress_bar=False,
        convert_to_numpy=True,
    )
    return np.asarray(embeddings, dtype=np.float32)


def cluster_titles(embeddings: np.ndarray, similarity_threshold: float) -> np.ndarray:
    if len(embeddings) == 1:
        return np.array([0], dtype=np.int32)

    distance_threshold = max(0.0, min(1.0, 1.0 - similarity_threshold))
    clustering = AgglomerativeClustering(
        n_clusters=None,
        metric="cosine",
        linkage="average",
        distance_threshold=distance_threshold,
    )
    return clustering.fit_predict(embeddings)


def cosine_similarity_matrix_row(center: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    center_norm = np.linalg.norm(center)
    matrix_norm = np.linalg.norm(matrix, axis=1)
    denom = np.clip(center_norm * matrix_norm, 1e-12, None)
    return np.dot(matrix, center) / denom


def build_cluster_summaries(
    articles: list[dict[str, Any]],
    embeddings: np.ndarray,
    labels: np.ndarray,
    now_dt: datetime,
) -> list[dict[str, Any]]:
    grouped_indexes: dict[int, list[int]] = defaultdict(list)
    for idx, label in enumerate(labels):
        grouped_indexes[int(label)].append(idx)

    cluster_summaries: list[dict[str, Any]] = []
    for cluster_number, indexes in grouped_indexes.items():
        cluster_articles = [articles[i] for i in indexes]
        cluster_embeddings = embeddings[indexes]
        article_times = [resolve_article_time(article) or now_dt for article in cluster_articles]
        hours_diffs = [(now_dt - article_time).total_seconds() / 3600 for article_time in article_times]
        recency_score = sum(math.exp(-DECAY_LAMBDA * max(hours_diff, 0.0)) for hours_diff in hours_diffs)
        unique_articles = len(cluster_articles)
        total_observations = sum(int(article.get("seen_count") or 1) for article in cluster_articles)
        heat_observations = sum(
            int(article.get("seen_count") or 1)
            if normalize_text(article.get("source_type")) == "newsnow"
            else 1
            for article in cluster_articles
        )
        unique_sources = len({normalize_text(article["source_name"]) for article in cluster_articles})
        heat_score = heat_observations + 0.75 * unique_sources + recency_score
        cluster_articles_sorted = sorted(
            cluster_articles,
            key=lambda article: resolve_article_time(article) or now_dt,
            reverse=True,
        )

        center = cluster_embeddings.mean(axis=0)
        similarities = cosine_similarity_matrix_row(center, cluster_embeddings)
        ranked_indexes = np.argsort(-similarities)

        representative_titles: list[str] = []
        seen_titles: set[str] = set()
        for local_index in ranked_indexes:
            title = cluster_articles[int(local_index)]["title"]
            if title not in seen_titles:
                representative_titles.append(title)
                seen_titles.add(title)
            if len(representative_titles) >= REPRESENTATIVE_TITLES_PER_CLUSTER:
                break

        cluster_summaries.append(
            {
                "cluster_id": f"c_{cluster_number}",
                "heat_score": round(heat_score, 4),
                "total_articles": unique_articles,
                "total_observations": total_observations,
                "heat_observations": heat_observations,
                "unique_sources": unique_sources,
                "first_seen": min(article_times).strftime("%Y-%m-%d %H:%M:%S %z"),
                "latest_seen": max(article_times).strftime("%Y-%m-%d %H:%M:%S %z"),
                "representative_titles": representative_titles,
                "sources": sorted({normalize_text(article["source_name"]) for article in cluster_articles}),
                "article_ids": [int(article["id"]) for article in cluster_articles_sorted],
                "articles": [
                    {
                        "id": int(article["id"]),
                        "title": article["title"],
                        "url": article.get("url"),
                        "source_name": article["source_name"],
                        "published_at": article.get("published_at"),
                        "fetched_at": article.get("fetched_at"),
                        "resolved_time": article.get("resolved_time"),
                        "first_seen": article.get("first_seen"),
                        "last_seen": article.get("last_seen"),
                        "seen_count": article.get("seen_count", 1),
                        "active_hours": article.get("active_hours", 0.0),
                        "latest_rank": article.get("latest_rank"),
                        "source_count": article.get("source_count", 1),
                    }
                    for article in cluster_articles_sorted
                ],
            }
        )

    cluster_summaries.sort(
        key=lambda cluster: (
            -cluster["heat_score"],
            -cluster["unique_sources"],
            -cluster["total_observations"],
            -cluster["total_articles"],
            cluster["cluster_id"],
        )
    )

    for rank, cluster in enumerate(cluster_summaries[:TOP_N_CLUSTERS], start=1):
        cluster["rank"] = rank

    return cluster_summaries[:TOP_N_CLUSTERS]


def build_data_coverage(
    articles: list[dict[str, Any]],
    window_start_dt: datetime,
    window_end_dt: datetime,
    requested_window_hours: int,
) -> dict[str, Any]:
    article_times = [
        resolve_article_time(article)
        for article in articles
    ]
    valid_times = [article_time for article_time in article_times if article_time is not None]
    if not valid_times:
        return {
            "requested_window_hours": requested_window_hours,
            "actual_covered_hours": 0.0,
            "coverage_complete": False,
            "window_start_time": window_start_dt.strftime("%Y-%m-%d %H:%M:%S %z"),
            "window_end_time": window_end_dt.strftime("%Y-%m-%d %H:%M:%S %z"),
            "earliest_item_time": None,
            "latest_item_time": None,
        }

    earliest_time = min(valid_times)
    latest_time = max(valid_times)
    coverage_start_time = earliest_time - timedelta(hours=COLLECTION_INTERVAL_HOURS)
    actual_covered_hours = max(0.0, (window_end_dt - coverage_start_time).total_seconds() / 3600)
    return {
        "requested_window_hours": requested_window_hours,
        "actual_covered_hours": round(min(actual_covered_hours, float(requested_window_hours)), 2),
        "coverage_complete": actual_covered_hours >= requested_window_hours * 0.95,
        "window_start_time": window_start_dt.strftime("%Y-%m-%d %H:%M:%S %z"),
        "window_end_time": window_end_dt.strftime("%Y-%m-%d %H:%M:%S %z"),
        "coverage_start_time": coverage_start_time.strftime("%Y-%m-%d %H:%M:%S %z"),
        "coverage_interval_hours": COLLECTION_INTERVAL_HOURS,
        "earliest_item_time": earliest_time.strftime("%Y-%m-%d %H:%M:%S %z"),
        "latest_item_time": latest_time.strftime("%Y-%m-%d %H:%M:%S %z"),
    }


def output_file_path(base_dir: Path, source_type: str, now_dt: datetime, analysis_window_hours: int) -> Path:
    output_dir = base_dir / OUTPUT_DIR / source_type
    output_dir.mkdir(parents=True, exist_ok=True)
    prefix = OUTPUT_FILE_PREFIX_MAP[source_type]
    filename = f"{prefix}_{now_dt.strftime('%Y-%m-%d_%H')}_w{analysis_window_hours}h.json"
    return output_dir / filename


def run_pipeline_for_source_type(
    base_dir: Path,
    source_type: str,
    analysis_window_hours: int = DEFAULT_ANALYSIS_WINDOW_HOURS,
) -> Path:
    now_dt = datetime.now(ZoneInfo(TIMEZONE))
    window_hours = normalize_window_hours(analysis_window_hours)
    window_start_dt, window_end_dt = resolve_complete_hour_window(now_dt, window_hours)
    raw_articles = load_articles_from_sqlite(base_dir, source_type, window_hours, now_dt)
    if not raw_articles:
        raise RuntimeError(f"No recent {source_type} news items were loaded from SQLite.")
    loaded_observations = sum(int(article.get("seen_count") or 1) for article in raw_articles)

    deduped_articles = deduplicate_articles(raw_articles)
    if not deduped_articles:
        raise RuntimeError(f"No {source_type} articles remained after deduplication.")

    filtered_articles = apply_source_quality_filter(base_dir, source_type, deduped_articles)
    if not filtered_articles:
        raise RuntimeError(f"No {source_type} articles remained after quality filtering.")

    event_scored_articles = len(filtered_articles)
    kept_articles = filtered_articles
    event_filtered_count = 0
    if source_type == "newsnow":
        kept_articles, event_filtered_count = apply_newsnow_event_score_filter(
            base_dir,
            source_type,
            filtered_articles,
        )
        print(f"[INFO] NewsNow event score filtered out {event_filtered_count} articles.")
        if not kept_articles:
            raise RuntimeError("No newsnow articles remained after news_event_score filtering.")

    config = get_source_config(source_type)
    titles = [article["title"] for article in kept_articles]
    embeddings = build_embeddings(titles, MODEL_NAME)
    labels = cluster_titles(embeddings, float(config["similarity_threshold"]))

    for index, article in enumerate(kept_articles):
        article["cluster_id"] = f"c_{int(labels[index])}"

    cluster_summaries = build_cluster_summaries(kept_articles, embeddings, labels, now_dt)
    data_coverage = build_data_coverage(raw_articles, window_start_dt, window_end_dt, window_hours)
    payload = {
        "generated_at": now_dt.strftime("%Y-%m-%d %H:%M:%S %z"),
        "source_type": source_type,
        "db_file": str((base_dir / DB_FILE).resolve()),
        "analysis_window_hours": window_hours,
        "analysis_window": {
            "mode": "last_complete_hours",
            "start_time": window_start_dt.strftime("%Y-%m-%d %H:%M:%S %z"),
            "end_time": window_end_dt.strftime("%Y-%m-%d %H:%M:%S %z"),
            "includes_start": True,
            "includes_end": False,
        },
        "data_coverage": data_coverage,
        "model_name": MODEL_NAME,
        "similarity_threshold": float(config["similarity_threshold"]),
        "decay_lambda": DECAY_LAMBDA,
        "top_n_clusters": TOP_N_CLUSTERS,
        "raw_articles": loaded_observations,
        "unique_loaded_articles": len(raw_articles),
        "deduped_articles": len(deduped_articles),
        "quality_filtered_articles": len(filtered_articles),
        "event_scored_articles": event_scored_articles,
        "event_kept_articles": len(kept_articles),
        "clusters": cluster_summaries,
        "total_clusters": len(set(int(label) for label in labels)),
    }

    path = output_file_path(base_dir, source_type, now_dt, window_hours)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run hot topic discovery from SQLite news_items.")
    parser.add_argument(
        "--window-hours",
        type=int,
        default=DEFAULT_ANALYSIS_WINDOW_HOURS,
        help="Only analyze news from the last N hours.",
    )
    parser.add_argument(
        "--source-type",
        choices=SUPPORTED_SOURCE_TYPES,
        help="Only run hot topic discovery for the specified source type.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    base_dir = PROJECT_ROOT
    output_paths: list[Path] = []
    source_types = [args.source_type] if args.source_type else list(SUPPORTED_SOURCE_TYPES)
    window_hours = normalize_window_hours(args.window_hours)

    for source_type in source_types:
        try:
            output_paths.append(run_pipeline_for_source_type(base_dir, source_type, window_hours))
        except RuntimeError as exc:
            message = str(exc)
            print(f"[WARN] {message}")

    for path in output_paths:
        print(f"[INFO] Hot topic pipeline output: {path}")

    if not output_paths:
        print("不存在要分析的数据，请检查sqlite文件。")


if __name__ == "__main__":
    main()
