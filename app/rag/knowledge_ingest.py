# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import math
import re
from datetime import datetime
from hashlib import sha1
from pathlib import Path
from typing import Any

from zoneinfo import ZoneInfo

from app.rag.knowledge_store import (
    CHUNKS_JSONL,
    DOCUMENTS_JSONL,
    KNOWLEDGE_PROCESSED_DIR,
    KNOWLEDGE_SOURCES_DIR,
)


TIMEZONE = "Asia/Shanghai"
CHUNK_SIZE = 1000
CHUNK_OVERLAP = 200
SUPPORTED_DOMAINS = {"geopolitics", "markets", "tech", "general"}


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def now_text() -> str:
    return datetime.now(ZoneInfo(TIMEZONE)).strftime("%Y-%m-%d %H:%M:%S %z")


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def detect_language(text: str) -> str:
    if not text:
        return "unknown"

    chinese_chars = len(re.findall(r"[\u4e00-\u9fff]", text))
    latin_chars = len(re.findall(r"[A-Za-z]", text))
    total_chars = chinese_chars + latin_chars
    if total_chars == 0:
        return "unknown"
    if chinese_chars / total_chars >= 0.2:
        return "zh"
    return "en"


def infer_title_from_filename(path: Path) -> str:
    return path.stem.replace("_", " ").replace("-", " ").strip() or path.stem


def infer_domain_from_path(source_path: Path, sources_dir: Path) -> str:
    relative_path = source_path.relative_to(sources_dir)
    top_level = relative_path.parts[0] if relative_path.parts else "general"
    return top_level if top_level in SUPPORTED_DOMAINS else "general"


def scan_source_files(sources_dir: Path) -> list[Path]:
    return sorted(path for path in sources_dir.rglob("*.txt") if path.is_file())


def build_document_id(source_path: Path) -> str:
    digest = sha1(str(source_path).encode("utf-8")).hexdigest()[:12]
    return f"doc_{digest}"


def build_chunk_id(document_id: str, chunk_index: int) -> str:
    return f"{document_id}_chunk_{chunk_index:04d}"


def build_document_record(base_dir: Path, source_path: Path, sources_dir: Path) -> dict[str, Any]:
    text = source_path.read_text(encoding="utf-8")
    document_id = build_document_id(source_path.relative_to(base_dir))
    domain = infer_domain_from_path(source_path, sources_dir)
    language = detect_language(text)
    return {
        "document_id": document_id,
        "source_path": str(source_path.relative_to(base_dir)),
        "domain": domain,
        "filename": source_path.name,
        "title": infer_title_from_filename(source_path),
        "source_type": "unknown",
        "language": language,
        "tags": [],
        "created_at": now_text(),
    }


def chunk_text(text: str) -> list[str]:
    normalized = text.strip()
    if not normalized:
        return []

    step = max(1, CHUNK_SIZE - CHUNK_OVERLAP)
    chunks: list[str] = []
    for start in range(0, len(normalized), step):
        chunk = normalized[start : start + CHUNK_SIZE].strip()
        if chunk:
            chunks.append(chunk)
        if start + CHUNK_SIZE >= len(normalized):
            break
    return chunks


def build_chunk_records(
    base_dir: Path,
    source_path: Path,
    document_record: dict[str, Any],
) -> list[dict[str, Any]]:
    text = source_path.read_text(encoding="utf-8")
    chunks = chunk_text(text)
    records: list[dict[str, Any]] = []

    for chunk_index, chunk in enumerate(chunks):
        records.append(
            {
                "chunk_id": build_chunk_id(document_record["document_id"], chunk_index),
                "document_id": document_record["document_id"],
                "domain": document_record["domain"],
                "title": document_record["title"],
                "source_path": str(source_path.relative_to(base_dir)),
                "chunk_index": chunk_index,
                "text": chunk,
                "char_count": len(chunk),
                "language": document_record["language"],
                "metadata": {
                    "filename": document_record["filename"],
                    "source_type": document_record["source_type"],
                    "tags": document_record["tags"],
                },
            }
        )

    return records


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        for record in records:
            file.write(json.dumps(record, ensure_ascii=False) + "\n")


def run_knowledge_ingest(base_dir: Path | None = None) -> dict[str, Any]:
    root_dir = base_dir or project_root()
    sources_dir = root_dir / KNOWLEDGE_SOURCES_DIR
    processed_dir = root_dir / KNOWLEDGE_PROCESSED_DIR
    processed_dir.mkdir(parents=True, exist_ok=True)

    source_files = scan_source_files(sources_dir)
    document_records: list[dict[str, Any]] = []
    chunk_records: list[dict[str, Any]] = []

    for source_path in source_files:
        document_record = build_document_record(root_dir, source_path, sources_dir)
        document_records.append(document_record)
        chunk_records.extend(build_chunk_records(root_dir, source_path, document_record))

    documents_path = root_dir / DOCUMENTS_JSONL
    chunks_path = root_dir / CHUNKS_JSONL
    write_jsonl(documents_path, document_records)
    write_jsonl(chunks_path, chunk_records)

    return {
        "source_count": len(source_files),
        "document_count": len(document_records),
        "chunk_count": len(chunk_records),
        "documents_path": documents_path,
        "chunks_path": chunks_path,
    }


def main() -> None:
    result = run_knowledge_ingest()
    print(f"[INFO] Scanned txt files: {result['source_count']}")
    print(f"[INFO] Generated documents: {result['document_count']}")
    print(f"[INFO] Generated chunks: {result['chunk_count']}")
    print(f"[INFO] Documents file: {result['documents_path']}")
    print(f"[INFO] Chunks file: {result['chunks_path']}")


if __name__ == "__main__":
    main()
