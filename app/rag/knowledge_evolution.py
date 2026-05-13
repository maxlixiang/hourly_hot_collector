# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import datetime
from hashlib import sha1
from pathlib import Path
from typing import Any

from zoneinfo import ZoneInfo

from app.rag.knowledge_store import CHUNKS_JSONL, DOCUMENTS_JSONL, KNOWLEDGE_ROOT


TIMEZONE = "Asia/Shanghai"
EVOLUTION_DIR = KNOWLEDGE_ROOT / "evolution"
VIEWPOINTS_JSONL = EVOLUTION_DIR / "viewpoints.jsonl"
VIEW_EVOLUTION_JSONL = EVOLUTION_DIR / "view_evolution.jsonl"
CHINESE_STOPWORDS = {
    "我们",
    "他们",
    "一个",
    "这种",
    "这个",
    "以及",
    "如果",
    "因为",
    "所以",
    "需要",
    "观察",
    "影响",
    "问题",
    "判断",
    "观点",
    "事实",
    "本期",
    "后续",
    "核心",
    "主要",
    "框架",
}
ENGLISH_STOPWORDS = {
    "the",
    "and",
    "that",
    "this",
    "with",
    "from",
    "into",
    "will",
    "have",
    "been",
    "more",
    "than",
    "about",
    "what",
    "when",
    "where",
    "which",
    "their",
    "they",
    "them",
    "could",
    "should",
    "would",
    "while",
    "over",
    "under",
}
TOPIC_ALIASES = {
    "iran": "iran",
    "iran_war": "iran",
    "us_iran_war": "iran",
    "hormuz": "hormuz",
    "strait_of_hormuz": "hormuz",
    "hormuz_strait": "hormuz",
    "oil": "oil",
    "oil_price": "oil",
    "petrodollar": "oil",
    "oil_rmb": "oil",
    "ukraine": "ukraine",
    "russia": "ukraine",
    "sanctions": "ukraine",
    "eu": "europe",
    "eurozone": "europe",
    "france": "europe",
    "deficit": "deficit",
    "growth": "growth",
    "taiwan": "taiwan",
    "cross_strait": "taiwan",
    "google": "ai",
    "nvidia": "ai",
    "openai": "ai",
    "chip": "chips",
    "chips": "chips",
    "gpu": "chips",
    "tpu": "chips",
    "semiconductor": "chips",
    "data_center": "data_centers",
    "data_centers": "data_centers",
    "gas": "gas_power",
    "gold": "gold",
    "silver": "silver",
    "de-dollarization": "dedollarization",
    "dedollarization": "dedollarization",
    "stocks": "equities",
    "equity": "equities",
    "equities": "equities",
    "market": "markets",
    "markets": "markets",
    "macro": "macro",
}
METHODOLOGY_MARKERS = ("判断框架", "framework", "method", "方法", "框架")
THESIS_MARKERS = ("主要观点", "view", "thesis", "核心观点", "主要判断")
TACTICAL_MARKERS = ("观察点", "watch", "impact", "影响", "后续")
EVENT_CALL_MARKERS = ("核心事实", "fact", "event", "事实", "call")
REINFORCE_POSITIVE = ("继续", "强化", "升级", "扩张", "上升", "看多", "rise", "reinforce", "expand")
REINFORCE_NEGATIVE = ("缓和", "下降", "逆转", "收缩", "看空", "decline", "ease", "reverse", "fall")


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def now_text() -> str:
    return datetime.now(ZoneInfo(TIMEZONE)).strftime("%Y-%m-%d %H:%M:%S %z")


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"Required file not found: {path}")
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if not line:
                continue
            records.append(json.loads(line))
    return records


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        for record in records:
            file.write(json.dumps(record, ensure_ascii=False) + "\n")


def extract_header_map(text: str) -> dict[str, str]:
    headers: dict[str, str] = {}
    for match in re.finditer(r"(?m)^#\s*([^:]+):\s*(.*)$", text):
        headers[normalize_text(match.group(1)).lower()] = normalize_text(match.group(2))
    return headers


def extract_sections(text: str) -> list[tuple[str, str]]:
    normalized = normalize_text(text)
    if not normalized:
        return []

    matches = list(re.finditer(r"(?m)^##\s*(.+)$", normalized))
    if not matches:
        body = re.sub(r"(?m)^#.*$", "", normalized).strip()
        return [("body", body)] if body else []

    sections: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        title = normalize_text(match.group(1))
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(normalized)
        content = normalize_text(normalized[start:end])
        if content:
            sections.append((title, content))
    return sections


def parse_date(text: str) -> str:
    value = normalize_text(text)
    if not value:
        return ""
    match = re.search(r"\d{4}-\d{2}-\d{2}", value)
    return match.group(0) if match else value


def simple_normalize(text: str) -> str:
    lowered = normalize_text(text).lower()
    lowered = re.sub(r"[_\-]+", " ", lowered)
    lowered = re.sub(r"[^\w\s\u4e00-\u9fff]", " ", lowered)
    lowered = re.sub(r"\s+", " ", lowered)
    return lowered.strip()


def extract_keywords(text: str, language: str, limit: int = 8) -> list[str]:
    normalized = simple_normalize(text)
    if not normalized:
        return []

    if language == "zh":
        phrases = re.findall(r"[\u4e00-\u9fff]{2,8}", normalized)
        filtered = [token for token in phrases if token not in CHINESE_STOPWORDS]
    else:
        words = re.findall(r"[a-zA-Z][a-zA-Z0-9_\-]{2,}", normalized)
        filtered = [token.lower() for token in words if token.lower() not in ENGLISH_STOPWORDS]

    deduped: list[str] = []
    for token in filtered:
        mapped = TOPIC_ALIASES.get(token, token)
        if mapped not in deduped:
            deduped.append(mapped)
        if len(deduped) >= limit:
            break
    return deduped


def infer_view_type(section_title: str, viewpoint_text: str) -> str:
    haystack = f"{section_title} {viewpoint_text}".lower()
    if any(marker.lower() in haystack for marker in METHODOLOGY_MARKERS):
        return "methodology"
    if any(marker.lower() in haystack for marker in THESIS_MARKERS):
        return "thesis"
    if any(marker.lower() in haystack for marker in TACTICAL_MARKERS):
        return "tactical_view"
    if any(marker.lower() in haystack for marker in EVENT_CALL_MARKERS):
        return "event_call"
    return "thesis"


def build_topic_key(domain: str, title: str, tags: list[str], keywords: list[str]) -> str:
    candidates: list[str] = []
    for item in tags + keywords + extract_keywords(title, "en", limit=6):
        normalized = simple_normalize(item).replace(" ", "_")
        if not normalized:
            continue
        mapped = TOPIC_ALIASES.get(normalized, normalized)
        if mapped not in candidates:
            candidates.append(mapped)
    if not candidates:
        candidates = ["general_topic"]
    return f"{domain}:{'_'.join(candidates[:3])}"


def build_viewpoint_id(document_id: str, chunk_id: str, section_title: str) -> str:
    digest = sha1(f"{document_id}|{chunk_id}|{section_title}".encode("utf-8")).hexdigest()[:12]
    return f"view_{digest}"


def section_confidence(section_title: str, content: str, expert: str, date_text: str) -> float:
    score = 0.45
    if expert and expert != "unknown":
        score += 0.15
    if date_text:
        score += 0.15
    if section_title and section_title != "body":
        score += 0.1
    if len(content) >= 120:
        score += 0.1
    if len(content) >= 240:
        score += 0.05
    return round(min(score, 0.95), 2)


def parse_tags(headers: dict[str, str], metadata: dict[str, Any]) -> list[str]:
    raw_tags = headers.get("tags") or ""
    if raw_tags:
        return [normalize_text(item) for item in raw_tags.split(",") if normalize_text(item)]
    metadata_tags = metadata.get("tags") or []
    return [normalize_text(item) for item in metadata_tags if normalize_text(item)]


def extract_viewpoints_from_chunk(
    chunk: dict[str, Any],
    document: dict[str, Any],
) -> list[dict[str, Any]]:
    text = normalize_text(chunk.get("text"))
    if not text:
        return []

    headers = extract_header_map(text)
    expert = headers.get("expert") or "unknown"
    title = headers.get("title") or normalize_text(document.get("title")) or normalize_text(chunk.get("title"))
    date_text = parse_date(headers.get("date") or "")
    source_type = headers.get("source_type") or normalize_text(document.get("source_type")) or "unknown"
    domain = normalize_text(chunk.get("domain") or document.get("domain")) or "general"
    source_path = normalize_text(chunk.get("source_path") or document.get("source_path"))
    language = normalize_text(chunk.get("language") or document.get("language")) or "unknown"
    tags = parse_tags(headers, chunk.get("metadata", {}))

    sections = extract_sections(text)
    viewpoints: list[dict[str, Any]] = []
    for section_title, section_content in sections:
        cleaned_content = normalize_text(section_content)
        if len(cleaned_content) < 40:
            continue

        preview = cleaned_content[:320]
        view_type = infer_view_type(section_title, preview)
        keywords = extract_keywords(" ".join([title] + tags + [preview]), language, limit=10)
        topic_key = build_topic_key(domain, title, tags, keywords)
        viewpoint_id = build_viewpoint_id(
            normalize_text(document.get("document_id")),
            normalize_text(chunk.get("chunk_id")),
            section_title,
        )
        viewpoints.append(
            {
                "viewpoint_id": viewpoint_id,
                "document_id": normalize_text(document.get("document_id")),
                "chunk_id": normalize_text(chunk.get("chunk_id")),
                "expert": expert,
                "title": title,
                "domain": domain,
                "date": date_text,
                "source_type": source_type,
                "source_path": source_path,
                "topic_key": topic_key,
                "view_type": view_type,
                "viewpoint_text": preview,
                "normalized_viewpoint": simple_normalize(preview),
                "keywords": keywords,
                "confidence": section_confidence(section_title, cleaned_content, expert, date_text),
            }
        )

    return viewpoints


def stance_score(text: str) -> int:
    lowered = simple_normalize(text)
    positive = sum(1 for marker in REINFORCE_POSITIVE if marker.lower() in lowered)
    negative = sum(1 for marker in REINFORCE_NEGATIVE if marker.lower() in lowered)
    return positive - negative


def keyword_overlap(previous: list[str], current: list[str]) -> float:
    previous_set = set(previous)
    current_set = set(current)
    if not previous_set or not current_set:
        return 0.0
    return len(previous_set & current_set) / max(1, len(previous_set | current_set))


def determine_evolution_status(previous: dict[str, Any], current: dict[str, Any]) -> tuple[str, str]:
    overlap = keyword_overlap(previous.get("keywords", []), current.get("keywords", []))
    previous_stance = stance_score(normalize_text(previous.get("viewpoint_text")))
    current_stance = stance_score(normalize_text(current.get("viewpoint_text")))

    if previous_stance and current_stance and (previous_stance > 0 > current_stance or previous_stance < 0 < current_stance):
        return "reversed", "Keyword overlap exists, but directional wording shifted to the opposite side."

    if overlap >= 0.45 and previous.get("view_type") == current.get("view_type"):
        return "reinforced", "Same expert repeated a highly similar viewpoint on the same topic."

    return "updated", "Topic remained similar, but the wording or emphasis changed without a full reversal."


def build_evolution_id(expert: str, topic_key: str, current_viewpoint_id: str, previous_viewpoint_id: str) -> str:
    digest = sha1(f"{expert}|{topic_key}|{current_viewpoint_id}|{previous_viewpoint_id}".encode("utf-8")).hexdigest()[:12]
    return f"evo_{digest}"


def parse_sort_date(value: str) -> tuple[int, str]:
    text = normalize_text(value)
    if not text:
        return (99999999, "")
    match = re.search(r"(\d{4})-(\d{2})-(\d{2})", text)
    if not match:
        return (99999999, text)
    return (int("".join(match.groups())), text)


def generate_viewpoints(documents: list[dict[str, Any]], chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    document_map = {normalize_text(item.get("document_id")): item for item in documents}
    viewpoints: list[dict[str, Any]] = []
    for chunk in chunks:
        document = document_map.get(normalize_text(chunk.get("document_id")))
        if not document:
            continue
        viewpoints.extend(extract_viewpoints_from_chunk(chunk, document))
    return viewpoints


def generate_evolution_records(viewpoints: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for viewpoint in viewpoints:
        expert = normalize_text(viewpoint.get("expert")) or "unknown"
        topic_key = normalize_text(viewpoint.get("topic_key")) or "general:general_topic"
        grouped[(expert, topic_key)].append(viewpoint)

    evolution_records: list[dict[str, Any]] = []
    for (expert, topic_key), items in grouped.items():
        ordered = sorted(
            items,
            key=lambda item: (
                parse_sort_date(normalize_text(item.get("date"))),
                normalize_text(item.get("document_id")),
                normalize_text(item.get("viewpoint_id")),
            ),
        )
        for previous, current in zip(ordered, ordered[1:]):
            status, reason = determine_evolution_status(previous, current)
            evolution_records.append(
                {
                    "evolution_id": build_evolution_id(
                        expert,
                        topic_key,
                        normalize_text(current.get("viewpoint_id")),
                        normalize_text(previous.get("viewpoint_id")),
                    ),
                    "expert": expert,
                    "topic_key": topic_key,
                    "current_viewpoint_id": normalize_text(current.get("viewpoint_id")),
                    "previous_viewpoint_id": normalize_text(previous.get("viewpoint_id")),
                    "evolution_status": status,
                    "reason": reason,
                    "current_date": normalize_text(current.get("date")),
                    "previous_date": normalize_text(previous.get("date")),
                    "current_view_type": normalize_text(current.get("view_type")),
                    "previous_view_type": normalize_text(previous.get("view_type")),
                }
            )
    return evolution_records


def run_knowledge_evolution(base_dir: Path | None = None) -> dict[str, Any]:
    root_dir = base_dir or project_root()
    documents_path = root_dir / DOCUMENTS_JSONL
    chunks_path = root_dir / CHUNKS_JSONL
    evolution_dir = root_dir / EVOLUTION_DIR
    evolution_dir.mkdir(parents=True, exist_ok=True)

    documents = load_jsonl(documents_path)
    chunks = load_jsonl(chunks_path)
    viewpoints = generate_viewpoints(documents, chunks)
    evolution_records = generate_evolution_records(viewpoints)

    viewpoints_path = root_dir / VIEWPOINTS_JSONL
    evolution_path = root_dir / VIEW_EVOLUTION_JSONL
    write_jsonl(viewpoints_path, viewpoints)
    write_jsonl(evolution_path, evolution_records)

    return {
        "document_count": len(documents),
        "chunk_count": len(chunks),
        "viewpoint_count": len(viewpoints),
        "evolution_count": len(evolution_records),
        "viewpoints_path": viewpoints_path,
        "evolution_path": evolution_path,
    }


def main() -> None:
    result = run_knowledge_evolution()
    print(f"[INFO] Scanned documents: {result['document_count']}")
    print(f"[INFO] Scanned chunks: {result['chunk_count']}")
    print(f"[INFO] Generated viewpoints: {result['viewpoint_count']}")
    print(f"[INFO] Generated evolution records: {result['evolution_count']}")
    print(f"[INFO] Viewpoints file: {result['viewpoints_path']}")
    print(f"[INFO] Evolution file: {result['evolution_path']}")


if __name__ == "__main__":
    main()
