# -*- coding: utf-8 -*-
from __future__ import annotations

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
ANALYSIS_WINDOW_HOURS = 6
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
NEWSNOW_EVENT_SCORE_THRESHOLD = 2

PIPELINE_CONFIG = {
    "newsnow": {
        "similarity_threshold": 0.42,
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
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    return connection


def resolve_article_time(article: dict[str, Any]) -> datetime | None:
    published_at = parse_datetime_object(article.get("published_at"))
    if published_at is not None:
        return published_at
    return parse_datetime_object(article.get("fetched_at"))


def load_articles_from_sqlite(base_dir: Path, source_type: str) -> list[dict[str, Any]]:
    db_path = base_dir / DB_FILE
    if not db_path.exists():
        raise FileNotFoundError(f"SQLite database not found: {db_path}")

    if source_type not in SUPPORTED_SOURCE_TYPES:
        raise ValueError(f"Unsupported source_type: {source_type}")

    now_dt = datetime.now(ZoneInfo(TIMEZONE))
    cutoff_dt = now_dt - timedelta(hours=ANALYSIS_WINDOW_HOURS)
    cutoff_text = cutoff_dt.strftime("%Y-%m-%d %H:%M:%S %z")

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
            language
        FROM news_items
        WHERE source_type = ?
          AND title IS NOT NULL
          AND TRIM(title) != ''
          AND (
                (published_at IS NOT NULL AND TRIM(published_at) != '' AND published_at >= ?)
             OR ((published_at IS NULL OR TRIM(published_at) = '') AND (fetched_at IS NOT NULL AND TRIM(fetched_at) != '' AND fetched_at >= ?))
          )
        ORDER BY COALESCE(published_at, fetched_at) DESC, id DESC
    """

    with get_connection(db_path) as connection:
        rows = connection.execute(query, (source_type, cutoff_text, cutoff_text)).fetchall()

    articles: list[dict[str, Any]] = []
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

        resolved_time = resolve_article_time(article)
        if resolved_time is None or resolved_time < cutoff_dt:
            continue
        article["resolved_time"] = resolved_time.strftime("%Y-%m-%d %H:%M:%S %z")
        articles.append(article)

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
        total_articles = len(cluster_articles)
        unique_sources = len({normalize_text(article["source_name"]) for article in cluster_articles})
        heat_score = total_articles + 0.75 * unique_sources + recency_score

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
                "total_articles": total_articles,
                "unique_sources": unique_sources,
                "first_seen": min(article_times).strftime("%Y-%m-%d %H:%M:%S %z"),
                "latest_seen": max(article_times).strftime("%Y-%m-%d %H:%M:%S %z"),
                "representative_titles": representative_titles,
                "sources": sorted({normalize_text(article["source_name"]) for article in cluster_articles}),
            }
        )

    cluster_summaries.sort(
        key=lambda cluster: (
            -cluster["heat_score"],
            -cluster["unique_sources"],
            -cluster["total_articles"],
            cluster["cluster_id"],
        )
    )

    for rank, cluster in enumerate(cluster_summaries[:TOP_N_CLUSTERS], start=1):
        cluster["rank"] = rank

    return cluster_summaries[:TOP_N_CLUSTERS]


def output_file_path(base_dir: Path, source_type: str, now_dt: datetime) -> Path:
    output_dir = base_dir / OUTPUT_DIR / source_type
    output_dir.mkdir(parents=True, exist_ok=True)
    prefix = OUTPUT_FILE_PREFIX_MAP[source_type]
    filename = f"{prefix}_{now_dt.strftime('%Y-%m-%d_%H')}.json"
    return output_dir / filename


def run_pipeline_for_source_type(base_dir: Path, source_type: str) -> Path:
    now_dt = datetime.now(ZoneInfo(TIMEZONE))
    raw_articles = load_articles_from_sqlite(base_dir, source_type)
    if not raw_articles:
        raise RuntimeError(f"No recent {source_type} news items were loaded from SQLite.")

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
    payload = {
        "generated_at": now_dt.strftime("%Y-%m-%d %H:%M:%S %z"),
        "source_type": source_type,
        "db_file": str((base_dir / DB_FILE).resolve()),
        "analysis_window_hours": ANALYSIS_WINDOW_HOURS,
        "model_name": MODEL_NAME,
        "similarity_threshold": float(config["similarity_threshold"]),
        "decay_lambda": DECAY_LAMBDA,
        "top_n_clusters": TOP_N_CLUSTERS,
        "raw_articles": len(raw_articles),
        "deduped_articles": len(deduped_articles),
        "quality_filtered_articles": len(filtered_articles),
        "event_scored_articles": event_scored_articles,
        "event_kept_articles": len(kept_articles),
        "clusters": cluster_summaries,
        "total_clusters": len(set(int(label) for label in labels)),
    }

    path = output_file_path(base_dir, source_type, now_dt)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def main() -> None:
    base_dir = PROJECT_ROOT
    output_paths: list[Path] = []

    for source_type in SUPPORTED_SOURCE_TYPES:
        try:
            output_paths.append(run_pipeline_for_source_type(base_dir, source_type))
        except RuntimeError as exc:
            message = str(exc)
            print(f"[WARN] {message}")

    for path in output_paths:
        print(f"[INFO] Hot topic pipeline output: {path}")

    if not output_paths:
        print("不存在要分析的数据，请检查sqlite文件。")


if __name__ == "__main__":
    main()
