# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from zoneinfo import ZoneInfo

from app.rag.knowledge_store import CHUNKS_JSONL


TIMEZONE = "Asia/Shanghai"
REPORT_DIR = "data/analysis/reports"
RETRIEVED_CONTEXT_DIR = "data/analysis/retrieved_context"
SUPPORTED_SOURCE_TYPES = ("newsnow", "rss")
REPORT_FILE_PREFIX_MAP = {
    "newsnow": "newsnow_basic_analysis_*.json",
    "rss": "rss_basic_analysis_*.json",
}
EVENT_TYPE_DOMAIN_MAP = {
    "geopolitics": {"geopolitics", "general"},
    "markets": {"markets", "general"},
    "tech": {"tech", "general"},
    "society": {"general"},
}
TOP_K = 5
MIN_SCORE = 1
TITLE_KEYWORD_WEIGHT = 3
FACT_KEYWORD_WEIGHT = 2
SUMMARY_KEYWORD_WEIGHT = 1
TITLE_HIT_SCORE = 4
TEXT_HIT_SCORE = 1
PRIMARY_DOMAIN_BONUS = 2
ALLOWED_DOMAIN_BONUS = 1
ENGLISH_STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with", "from", "by", "at",
    "is", "are", "was", "were", "be", "been", "this", "that", "these", "those", "it", "its",
    "as", "into", "about", "after", "before", "over", "under", "than", "also", "may", "could",
    "would", "should", "their", "them", "his", "her", "our", "your", "more", "most", "latest",
    "need", "needs", "watch", "point", "points", "possible", "impact", "impacts", "summary",
    "two", "now", "right", "good", "live", "update", "updates", "world", "global", "says", "said",
    "from", "past", "source", "sources", "title", "event", "events", "news", "related",
}
CHINESE_STOPWORDS = {
    "展开", "来自", "过去", "可能", "影响", "需要", "关注", "后续", "是否", "出现", "变化",
    "来源", "标题", "事件", "相关新闻", "主要", "包括", "代表性", "最新", "最早", "进展",
    "信号", "围绕", "相关", "条新", "小时内", "文章量", "来源数", "后续观察", "社会关注",
}
LOW_SIGNAL_SUMMARY_PHRASES = {
    "该事件围绕", "主要标题包括", "可能影响", "需要关注", "后续观察点",
}


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def now_text() -> str:
    return datetime.now(ZoneInfo(TIMEZONE)).strftime("%Y-%m-%d %H:%M:%S %z")


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"Knowledge chunks file not found: {path}")
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if not line:
                continue
            records.append(json.loads(line))
    return records


def find_latest_analysis_file(base_dir: Path, source_type: str) -> Path:
    pattern = REPORT_FILE_PREFIX_MAP[source_type]
    candidates = sorted((base_dir / REPORT_DIR).glob(pattern))
    if candidates:
        return candidates[-1]
    raise FileNotFoundError(f"No basic analysis file found for source_type={source_type}")


def clean_summary_text(summary: str) -> str:
    cleaned = normalize_text(summary)
    for phrase in LOW_SIGNAL_SUMMARY_PHRASES:
        cleaned = cleaned.replace(phrase, " ")
    return re.sub(r"\s+", " ", cleaned).strip()


def is_title_like_fact(text: str) -> bool:
    candidate = normalize_text(text)
    if not candidate:
        return False
    if "标题为" in candidate or "标题包括" in candidate:
        return True
    if re.search(r"[A-Za-z]{3,}", candidate):
        return True
    if len(candidate) >= 10 and ("，" in candidate or "：" in candidate):
        return True
    return False


def extract_title_like_text(text: str) -> str:
    candidate = normalize_text(text)
    if not candidate:
        return ""
    patterns = [
        r"标题为[“\"']?(.*?)[”\"']?$",
        r"标题包括[：: ]?[“\"']?(.*?)[”\"']?$",
    ]
    for pattern in patterns:
        match = re.search(pattern, candidate)
        if match:
            extracted = normalize_text(match.group(1))
            if extracted:
                return extracted
    return candidate


def build_query_parts(analysis: dict[str, Any]) -> dict[str, list[str] | str]:
    event_title = normalize_text(analysis.get("event_title"))
    summary = clean_summary_text(normalize_text(analysis.get("summary")))
    key_facts = [
        extract_title_like_text(item)
        for item in analysis.get("key_facts", [])
        if is_title_like_fact(normalize_text(item))
    ]
    key_facts = [item for item in key_facts if item]
    return {
        "event_title": event_title,
        "summary": summary,
        "key_facts": key_facts,
    }


def build_query_text(analysis: dict[str, Any]) -> str:
    parts = build_query_parts(analysis)
    text_parts: list[str] = [
        normalize_text(parts["event_title"]),
        normalize_text(parts["summary"]),
        " ".join(parts["key_facts"]),
    ]
    return " ".join(part for part in text_parts if part)


def extract_keywords(text: str) -> list[str]:
    chinese_phrases = [
        phrase
        for phrase in re.findall(r"[\u4e00-\u9fff]{2,12}", text)
        if phrase not in CHINESE_STOPWORDS
    ]
    english_words = []
    for word in re.findall(r"[A-Za-z][A-Za-z\.\-]{1,}", text):
        lowered = word.lower()
        if lowered in ENGLISH_STOPWORDS or len(lowered) < 3:
            continue
        english_words.append(word)

    raw_keywords = chinese_phrases + english_words
    keywords: list[str] = []
    seen: set[str] = set()
    for keyword in raw_keywords:
        normalized_keyword = normalize_text(keyword)
        key = normalized_keyword.lower()
        if not normalized_keyword or key in seen:
            continue
        seen.add(key)
        keywords.append(normalized_keyword)
    return keywords


def build_weighted_keywords(analysis: dict[str, Any]) -> list[tuple[str, int]]:
    parts = build_query_parts(analysis)
    weighted_keywords: list[tuple[str, int]] = []
    seen_weights: dict[str, int] = {}

    def add_keywords(text: str, weight: int) -> None:
        for keyword in extract_keywords(text):
            key = keyword.lower()
            seen_weights[key] = max(seen_weights.get(key, 0), weight)

    add_keywords(normalize_text(parts["event_title"]), TITLE_KEYWORD_WEIGHT)
    add_keywords(" ".join(parts["key_facts"]), FACT_KEYWORD_WEIGHT)
    add_keywords(normalize_text(parts["summary"]), SUMMARY_KEYWORD_WEIGHT)

    for key, weight in seen_weights.items():
        weighted_keywords.append((key, weight))

    keyword_map: dict[str, str] = {}
    for text in (
        normalize_text(parts["event_title"]),
        " ".join(parts["key_facts"]),
        normalize_text(parts["summary"]),
    ):
        for keyword in extract_keywords(text):
            keyword_map.setdefault(keyword.lower(), keyword)

    return [(keyword_map[key], weight) for key, weight in sorted(seen_weights.items(), key=lambda item: (-item[1], item[0]))]


def allowed_domains_for_event_type(event_type: str) -> set[str]:
    return EVENT_TYPE_DOMAIN_MAP.get(normalize_text(event_type), {"general"})


def primary_domain_for_event_type(event_type: str) -> str:
    normalized = normalize_text(event_type)
    if normalized in {"geopolitics", "markets", "tech", "general"}:
        return normalized
    return "general"


def keyword_in_text(keyword: str, text: str) -> bool:
    if not keyword or not text:
        return False
    if re.search(r"[A-Za-z]", keyword):
        pattern = rf"\b{re.escape(keyword)}\b"
        return re.search(pattern, text, flags=re.IGNORECASE) is not None
    return keyword in text


def score_chunk(
    chunk: dict[str, Any],
    keywords: list[tuple[str, int]],
    allowed_domains: set[str],
    primary_domain: str,
) -> tuple[int, list[str]]:
    score = 0
    matched_keywords: list[str] = []
    text = normalize_text(chunk.get("text"))
    title = normalize_text(chunk.get("title"))
    domain = normalize_text(chunk.get("domain"))

    if domain == primary_domain:
        score += PRIMARY_DOMAIN_BONUS
    elif domain in allowed_domains:
        score += ALLOWED_DOMAIN_BONUS

    seen_matches: set[str] = set()
    for keyword, weight in keywords:
        matched_in_text = keyword_in_text(keyword, text)
        matched_in_title = keyword_in_text(keyword, title)
        if matched_in_text:
            score += TEXT_HIT_SCORE * weight
        if matched_in_title:
            score += TITLE_HIT_SCORE * weight
        if matched_in_text or matched_in_title:
            lowered = keyword.lower()
            if lowered not in seen_matches:
                matched_keywords.append(keyword)
                seen_matches.add(lowered)

    score += len(matched_keywords)
    return score, matched_keywords


def retrieve_chunks_for_analysis(
    analysis: dict[str, Any],
    chunks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    event_type = normalize_text(analysis.get("event_type")) or "society"
    allowed_domains = allowed_domains_for_event_type(event_type)
    primary_domain = primary_domain_for_event_type(event_type)
    keywords = build_weighted_keywords(analysis)
    candidates: list[dict[str, Any]] = []

    def collect_candidates(filter_domains: set[str]) -> list[dict[str, Any]]:
        current_candidates: list[dict[str, Any]] = []
        for chunk in chunks:
            domain = normalize_text(chunk.get("domain"))
            if domain not in filter_domains:
                continue

            score, matched_keywords = score_chunk(chunk, keywords, filter_domains, primary_domain)
            if score < MIN_SCORE or not matched_keywords:
                continue

            current_candidates.append(
                {
                    "chunk_id": chunk.get("chunk_id"),
                    "document_id": chunk.get("document_id"),
                    "domain": domain,
                    "title": normalize_text(chunk.get("title")),
                    "source_path": normalize_text(chunk.get("source_path")),
                    "chunk_index": chunk.get("chunk_index"),
                    "score": score,
                    "matched_keywords": matched_keywords,
                    "text": normalize_text(chunk.get("text")),
                }
            )
        return current_candidates

    candidates = collect_candidates(allowed_domains)
    if event_type == "society" and not candidates:
        candidates = collect_candidates({"geopolitics", "markets", "tech", "general"})

    candidates.sort(
        key=lambda item: (
            -int(item["score"]),
            -len(item["matched_keywords"]),
            len(normalize_text(item["text"])),
            normalize_text(item["chunk_id"]),
        )
    )
    return candidates[:TOP_K]


def build_output_path(base_dir: Path, source_type: str, generated_at: datetime) -> Path:
    output_dir = base_dir / RETRIEVED_CONTEXT_DIR
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir / f"{source_type}_retrieved_context_{generated_at.strftime('%Y-%m-%d_%H')}.json"


def build_retrieved_context_for_source_type(
    base_dir: Path,
    source_type: str,
    input_file: Path | None = None,
) -> Path:
    analysis_file = input_file or find_latest_analysis_file(base_dir, source_type)
    analysis_payload = load_json(analysis_file)
    chunks_path = base_dir / CHUNKS_JSONL
    chunks = load_jsonl(chunks_path)

    items: list[dict[str, Any]] = []
    for analysis in analysis_payload.get("analyses", []):
        query_text = build_query_text(analysis)
        retrieved_chunks = retrieve_chunks_for_analysis(analysis, chunks)
        items.append(
            {
                "cluster_id": analysis.get("cluster_id"),
                "rank": analysis.get("rank"),
                "event_title": analysis.get("event_title"),
                "event_type": analysis.get("event_type"),
                "query_text": query_text,
                "retrieved_chunks": retrieved_chunks,
            }
        )

    generated_at = datetime.now(ZoneInfo(TIMEZONE))
    output_payload = {
        "generated_at": generated_at.strftime("%Y-%m-%d %H:%M:%S %z"),
        "source_type": source_type,
        "source_analysis_file": str(analysis_file),
        "retrieval_count": len(items),
        "top_k": TOP_K,
        "items": items,
    }
    output_path = build_output_path(base_dir, source_type, generated_at)
    output_path.write_text(json.dumps(output_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Retrieve relevant knowledge chunks for basic analysis results.")
    parser.add_argument(
        "--source-type",
        choices=SUPPORTED_SOURCE_TYPES,
        help="Only retrieve context for the specified source type.",
    )
    parser.add_argument(
        "--input-file",
        help="Use a specific basic analysis JSON file instead of auto-detecting the latest one.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    base_dir = project_root()
    input_file = Path(args.input_file).resolve() if args.input_file else None

    if args.source_type and input_file is not None:
        payload = load_json(input_file)
        inferred_source_type = normalize_text(payload.get("source_type"))
        if inferred_source_type != args.source_type:
            raise ValueError(
                f"--source-type {args.source_type} does not match input file source_type {inferred_source_type}"
            )
        source_types = [args.source_type]
    elif args.source_type:
        source_types = [args.source_type]
    elif input_file is not None:
        payload = load_json(input_file)
        inferred_source_type = normalize_text(payload.get("source_type"))
        if inferred_source_type not in SUPPORTED_SOURCE_TYPES:
            raise ValueError("Unable to infer source_type from input analysis file.")
        source_types = [inferred_source_type]
    else:
        source_types = list(SUPPORTED_SOURCE_TYPES)

    for source_type in source_types:
        source_input_file = input_file if input_file is not None else None
        output_path = build_retrieved_context_for_source_type(base_dir, source_type, source_input_file)
        print(f"[INFO] Retrieved context output: {output_path}")


if __name__ == "__main__":
    main()
