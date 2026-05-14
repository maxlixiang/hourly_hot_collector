# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Literal, TypedDict


ContentFetchStatus = Literal["success", "summary_only", "failed"]
ContentQuality = Literal["full_text", "partial_text", "thin_text", "none"]


class ArticleReaderResult(TypedDict, total=False):
    url: str
    status: str
    content_fetch_status: ContentFetchStatus | str
    content_quality: ContentQuality | str
    http_status: int | None
    error: str
    title: str
    text: str
    text_excerpt: str
    char_count: int
    extraction_source: str
    reader_url: str
    jina_reader_error: str
    extractor_version: str
    fetched_at: str
    from_cache: bool
    cache_file: str
