"""Knowledge storage paths and constants for RAG preprocessing."""

from pathlib import Path


KNOWLEDGE_ROOT = Path("data/knowledge")
KNOWLEDGE_SOURCES_DIR = KNOWLEDGE_ROOT / "sources"
KNOWLEDGE_PROCESSED_DIR = KNOWLEDGE_ROOT / "processed"
DOCUMENTS_JSONL = KNOWLEDGE_PROCESSED_DIR / "documents.jsonl"
CHUNKS_JSONL = KNOWLEDGE_PROCESSED_DIR / "chunks.jsonl"
