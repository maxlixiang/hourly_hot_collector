# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import requests
from zoneinfo import ZoneInfo

try:
    import trafilatura  # type: ignore
except ImportError:  # pragma: no cover - optional dependency
    trafilatura = None

try:
    from newspaper import Article  # type: ignore
except ImportError:  # pragma: no cover - optional dependency
    Article = None


TIMEZONE = "Asia/Shanghai"
ARTICLE_CACHE_DIR = "data/agent/article_cache"
RUNTIME_FALLBACK_DIR = ".agent_runtime"
DEFAULT_TIMEOUT_SECONDS = 8
MAX_STORED_TEXT_CHARS = 6000
EXTRACTOR_VERSION = "article_reader_v4_quality_chain"
JINA_READER_BASE_URL = "https://r.jina.ai"
JINA_READER_TIMEOUT_SECONDS = 20
MIN_READER_TEXT_CHARS = 160
FULL_TEXT_MIN_CHARS = 700
PARTIAL_TEXT_MIN_CHARS = 220
SUMMARY_ONLY_REASONS = ("403", "401", "forbidden", "paywall", "too little readable text")
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36 hourly-hot-collector-agent/1.0"
)


def now_text() -> str:
    return datetime.now(ZoneInfo(TIMEZONE)).strftime("%Y-%m-%d %H:%M:%S %z")


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def truncate_text(text: str, limit: int) -> str:
    clean = normalize_text(text)
    if len(clean) <= limit:
        return clean
    return clean[: limit - 1].rstrip() + "…"


class ReadableHTMLParser(HTMLParser):
    """Small stdlib-only readable text extractor for article-reader v1."""

    SKIP_TAGS = {"script", "style", "noscript", "svg", "canvas", "iframe"}
    BLOCK_TAGS = {
        "article",
        "section",
        "p",
        "div",
        "li",
        "blockquote",
        "h1",
        "h2",
        "h3",
        "h4",
        "br",
    }

    def __init__(self) -> None:
        super().__init__()
        self.skip_depth = 0
        self.in_title = False
        self.in_jsonld = False
        self.article_depth = 0
        self.paragraph_depth = 0
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []
        self.article_parts: list[str] = []
        self.paragraph_parts: list[str] = []
        self.paragraphs: list[str] = []
        self.jsonld_parts: list[str] = []
        self.meta_description = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attr_map = {name.lower(): value or "" for name, value in attrs}
        if tag == "meta":
            meta_name = (attr_map.get("name") or attr_map.get("property") or "").lower()
            if meta_name in {"description", "og:description", "twitter:description"}:
                self.meta_description = normalize_text(unescape(attr_map.get("content", "")))
            return
        if tag == "script" and "ld+json" in attr_map.get("type", "").lower():
            self.in_jsonld = True
            return
        if tag in self.SKIP_TAGS:
            self.skip_depth += 1
            return
        if tag == "title":
            self.in_title = True
            return
        if tag == "article":
            self.article_depth += 1
        if tag == "p":
            self.paragraph_depth += 1
            self.paragraph_parts = []
        if tag in self.BLOCK_TAGS:
            self.text_parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "script" and self.in_jsonld:
            self.in_jsonld = False
            return
        if tag in self.SKIP_TAGS and self.skip_depth:
            self.skip_depth -= 1
            return
        if tag == "title":
            self.in_title = False
            return
        if tag == "p" and self.paragraph_depth:
            paragraph = normalize_text(" ".join(self.paragraph_parts))
            if paragraph:
                self.paragraphs.append(paragraph)
            self.paragraph_parts = []
            self.paragraph_depth -= 1
        if tag == "article" and self.article_depth:
            self.article_depth -= 1
        if tag in self.BLOCK_TAGS:
            self.text_parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self.in_jsonld:
            self.jsonld_parts.append(data)
            return
        if self.skip_depth:
            return
        text = normalize_text(unescape(data))
        if not text:
            return
        if self.in_title:
            self.title_parts.append(text)
            return
        if self.paragraph_depth:
            self.paragraph_parts.append(text)
        if self.article_depth:
            self.article_parts.append(text)
        self.text_parts.append(text)

    @property
    def title(self) -> str:
        return truncate_text(" ".join(self.title_parts), 240)

    @property
    def text(self) -> str:
        text = "\n".join(part.strip() for part in self.text_parts if part.strip())
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = re.sub(r"[ \t]{2,}", " ", text)
        return text.strip()

    @property
    def article_text(self) -> str:
        return normalize_text(" ".join(self.article_parts))

    @property
    def jsonld_text(self) -> str:
        return "\n".join(self.jsonld_parts)


def article_cache_key(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def article_cache_path(base_dir: Path, url: str, fallback: bool = False) -> Path:
    cache_dir = RUNTIME_FALLBACK_DIR if fallback else ARTICLE_CACHE_DIR
    return base_dir / cache_dir / f"{article_cache_key(url)}.json"


def load_cached_article(base_dir: Path, url: str) -> dict[str, Any] | None:
    for fallback in (False, True):
        path = article_cache_path(base_dir, url, fallback=fallback)
        if path.exists():
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload.get("status") != "success":
                continue
            if payload.get("extractor_version") != EXTRACTOR_VERSION:
                continue
            payload["from_cache"] = True
            payload["cache_file"] = str(path)
            return payload
    return None


def write_cached_article(base_dir: Path, url: str, payload: dict[str, Any]) -> Path | None:
    for fallback in (False, True):
        path = article_cache_path(base_dir, url, fallback=fallback)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            return path
        except OSError as exc:
            if fallback:
                print(f"[WARN] Unable to write article cache {path}: {exc}")
    return None


def infer_content_quality(text: str) -> str:
    clean_len = len(normalize_text(text))
    if clean_len >= FULL_TEXT_MIN_CHARS:
        return "full_text"
    if clean_len >= PARTIAL_TEXT_MIN_CHARS:
        return "partial_text"
    if clean_len > 0:
        return "thin_text"
    return "none"


def infer_failure_status(error: Any, http_status: int | None = None) -> str:
    error_text = normalize_text(error).lower()
    if http_status in {401, 402, 403, 451}:
        return "summary_only"
    if any(reason in error_text for reason in SUMMARY_ONLY_REASONS):
        return "summary_only"
    return "failed"


def success_result(
    *,
    url: str,
    title: str,
    text: str,
    http_status: int | None,
    extraction_source: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    clean_text = truncate_text(text, MAX_STORED_TEXT_CHARS)
    payload = {
        "url": url,
        "status": "success",
        "content_fetch_status": "success",
        "content_quality": infer_content_quality(clean_text),
        "http_status": http_status,
        "error": "",
        "title": truncate_text(title, 240),
        "text": clean_text,
        "text_excerpt": build_text_excerpt(clean_text),
        "char_count": len(clean_text),
        "extraction_source": extraction_source,
        "extractor_version": EXTRACTOR_VERSION,
        "fetched_at": now_text(),
        "from_cache": False,
    }
    if extra:
        payload.update(extra)
    return payload


BOILERPLATE_PATTERNS = (
    "skip links",
    "skip to content",
    "navigation menu",
    "sign up",
    "click here to search",
    "show more",
    "advertisement",
    "cookies",
    "subscribe",
)


def looks_like_boilerplate(text: str) -> bool:
    lowered = normalize_text(text).lower()
    if not lowered:
        return True
    if any(pattern in lowered for pattern in BOILERPLATE_PATTERNS):
        return True
    words = lowered.split()
    if len(words) <= 4 and len(lowered) < 35:
        return True
    return False


def filter_paragraphs(paragraphs: list[str]) -> list[str]:
    filtered: list[str] = []
    seen: set[str] = set()
    for paragraph in paragraphs:
        clean = normalize_text(paragraph)
        if len(clean) < 45 or looks_like_boilerplate(clean):
            continue
        key = clean.lower()
        if key in seen:
            continue
        seen.add(key)
        filtered.append(clean)
    return filtered


def extract_jsonld_article_fields(jsonld_text: str) -> tuple[str, str]:
    title = ""
    text = ""
    if not normalize_text(jsonld_text):
        return title, text

    # Try strict JSON first, then fall back to small field extraction.
    try:
        payload = json.loads(jsonld_text)
        candidates = payload if isinstance(payload, list) else [payload]
        queue = list(candidates)
        while queue:
            current = queue.pop(0)
            if isinstance(current, list):
                queue.extend(current)
                continue
            if not isinstance(current, dict):
                continue
            graph = current.get("@graph")
            if isinstance(graph, list):
                queue.extend(graph)
            current_title = normalize_text(current.get("headline") or current.get("name"))
            current_body = normalize_text(current.get("articleBody") or current.get("description"))
            if current_title and not title:
                title = current_title
            if current_body and len(current_body) > len(text):
                text = current_body
    except json.JSONDecodeError:
        title_match = re.search(r'"(?:headline|name)"\s*:\s*"([^"]{10,300})"', jsonld_text, flags=re.IGNORECASE)
        body_match = re.search(
            r'"(?:articleBody|description)"\s*:\s*"(.{80,}?)"\s*,\s*"',
            jsonld_text,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if title_match:
            title = normalize_text(unescape(title_match.group(1)))
        if body_match:
            text = normalize_text(unescape(body_match.group(1)))
    return title, text


def choose_best_text_candidate(candidates: list[tuple[str, str]]) -> tuple[str, str]:
    best_label = ""
    best_text = ""
    for label, text in candidates:
        clean = normalize_text(text)
        if len(clean) < 120 or looks_like_boilerplate(clean[:700]):
            continue
        if len(clean) > len(best_text):
            best_label = label
            best_text = clean
    return best_label, best_text


def extract_readable_html(html_text: str) -> tuple[str, str, str]:
    parser = ReadableHTMLParser()
    parser.feed(html_text)
    jsonld_title, jsonld_text = extract_jsonld_article_fields(parser.jsonld_text)
    paragraphs = filter_paragraphs(parser.paragraphs)
    paragraph_text = "\n\n".join(paragraphs[:12])

    source, text = choose_best_text_candidate(
        [
            ("jsonld", jsonld_text),
            ("article_tag", parser.article_text),
            ("paragraphs", paragraph_text),
            ("meta_description", parser.meta_description),
            ("whole_page", parser.text),
        ]
    )
    title = parser.title or jsonld_title
    return title, text, source or "unknown"


def extract_with_trafilatura(html_text: str, url: str) -> tuple[str, str]:
    if trafilatura is None:
        return "", ""
    try:
        metadata = trafilatura.extract_metadata(html_text, default_url=url)
        title = normalize_text(getattr(metadata, "title", "")) if metadata else ""
        text = trafilatura.extract(
            html_text,
            url=url,
            include_comments=False,
            include_tables=False,
            output_format="txt",
            favor_recall=True,
        )
    except Exception as exc:  # pragma: no cover - extractor-specific defensive guard
        print(f"[WARN] trafilatura extraction failed for {url}: {exc}")
        return "", ""
    return title, normalize_text(text)


def extract_with_newspaper(html_text: str, url: str) -> tuple[str, str]:
    if Article is None:
        return "", ""
    try:
        article = Article(url=url)
        article.set_html(html_text)
        article.parse()
    except Exception as exc:  # pragma: no cover - extractor-specific defensive guard
        print(f"[WARN] newspaper4k extraction failed for {url}: {exc}")
        return "", ""
    return normalize_text(getattr(article, "title", "")), normalize_text(getattr(article, "text", ""))


def extract_direct_html_candidates(html_text: str, url: str) -> list[tuple[str, str, str]]:
    candidates: list[tuple[str, str, str]] = []

    trafilatura_title, trafilatura_text = extract_with_trafilatura(html_text, url)
    if trafilatura_text:
        candidates.append(("trafilatura", trafilatura_title, trafilatura_text))

    newspaper_title, newspaper_text = extract_with_newspaper(html_text, url)
    if newspaper_text:
        candidates.append(("newspaper4k", newspaper_title, newspaper_text))

    local_title, local_text, local_source = extract_readable_html(html_text)
    if local_text:
        candidates.append((f"local_html:{local_source}", local_title, local_text))

    return candidates


def build_jina_reader_url(url: str) -> str:
    return f"{JINA_READER_BASE_URL.rstrip('/')}/{url}"


def parse_jina_reader_text(markdown_text: str) -> tuple[str, str]:
    title = ""
    lines = markdown_text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    content_start: int | None = None

    for index, line in enumerate(lines):
        clean = line.strip()
        lowered = clean.lower()
        if lowered.startswith("title:") and not title:
            title = normalize_text(clean.split(":", 1)[1])
        if lowered.startswith("markdown content:"):
            content_start = index + 1
            break

    if content_start is None:
        body_lines = [
            line
            for line in lines
            if not line.strip().lower().startswith(
                (
                    "title:",
                    "url source:",
                    "published time:",
                    "warning:",
                )
            )
        ]
    else:
        body_lines = lines[content_start:]

    body = "\n".join(line.strip() for line in body_lines if line.strip())
    body = re.sub(r"\n{3,}", "\n\n", body)
    body = normalize_text(body)
    return title, body


def fetch_with_jina_reader(url: str, timeout: int = JINA_READER_TIMEOUT_SECONDS) -> dict[str, Any]:
    reader_url = build_jina_reader_url(url)
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/plain, text/markdown, */*",
    }
    try:
        response = requests.get(reader_url, headers=headers, timeout=timeout)
        http_status = response.status_code
        response.raise_for_status()
    except requests.RequestException as exc:
        return failed_result(url, f"jina reader failed: {exc}", getattr(getattr(exc, "response", None), "status_code", None))

    title, text = parse_jina_reader_text(response.text)
    if len(normalize_text(text)) < MIN_READER_TEXT_CHARS:
        return failed_result(url, "jina reader returned too little readable text", http_status)

    return success_result(
        url=url,
        title=title,
        text=text,
        http_status=http_status,
        extraction_source="jina_reader",
        extra={"reader_url": reader_url},
    )


def build_text_excerpt(text: str, limit: int = 600) -> str:
    clean = normalize_text(text)
    if not clean:
        return ""

    # Prefer sentence-like boundaries, but keep the implementation dependency-free.
    sentence_pattern = r"[^。！？.!?]{20,}[。！？.!?]"
    sentences = re.findall(sentence_pattern, clean)
    if sentences:
        excerpt = " ".join(sentences[:3])
        return truncate_text(excerpt, limit)
    return truncate_text(clean, limit)


def failed_result(url: str, error: str, http_status: int | None = None) -> dict[str, Any]:
    content_fetch_status = infer_failure_status(error, http_status)
    return {
        "url": url,
        "status": "failed",
        "content_fetch_status": content_fetch_status,
        "content_quality": "none",
        "http_status": http_status,
        "error": error,
        "fetched_at": now_text(),
        "from_cache": False,
    }


def read_article(
    url: str,
    base_dir: Path,
    *,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    use_cache: bool = True,
    max_stored_text_chars: int = MAX_STORED_TEXT_CHARS,
) -> dict[str, Any]:
    """Fetch a single article URL, extract lightweight text, and cache the result."""

    clean_url = normalize_text(url)
    if not clean_url:
        return failed_result("", "empty url")

    if use_cache:
        cached = load_cached_article(base_dir, clean_url)
        if cached is not None:
            return cached

    jina_payload = fetch_with_jina_reader(clean_url)
    if jina_payload.get("status") == "success":
        cache_file = write_cached_article(base_dir, clean_url, jina_payload)
        if cache_file:
            jina_payload["cache_file"] = str(cache_file)
        return jina_payload

    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }

    try:
        response = requests.get(clean_url, headers=headers, timeout=timeout)
        http_status = response.status_code
        response.raise_for_status()
    except requests.RequestException as exc:
        return failed_result(clean_url, str(exc), getattr(getattr(exc, "response", None), "status_code", None))

    content_type = response.headers.get("content-type", "")
    if "text/html" not in content_type and "application/xhtml" not in content_type and "<html" not in response.text[:500].lower():
        return failed_result(clean_url, f"unsupported content type: {content_type}", http_status)

    candidates = extract_direct_html_candidates(response.text, clean_url)
    best_source = ""
    best_title = ""
    best_text = ""
    for extraction_source, title, text in candidates:
        clean_text = normalize_text(text)
        if len(clean_text) > len(best_text):
            best_source = extraction_source
            best_title = title
            best_text = clean_text

    if best_text:
        payload = success_result(
            url=clean_url,
            title=best_title,
            text=truncate_text(best_text, max_stored_text_chars),
            http_status=http_status,
            extraction_source=best_source,
            extra={"jina_reader_error": normalize_text(jina_payload.get("error"))},
        )
    else:
        payload = failed_result(
            clean_url,
            f"no readable article text extracted; {jina_payload.get('error')}",
            http_status,
        )
        payload["jina_reader_error"] = normalize_text(jina_payload.get("error"))

    if payload["status"] == "success":
        cache_file = write_cached_article(base_dir, clean_url, payload)
        if cache_file:
            payload["cache_file"] = str(cache_file)
    return payload


def summarize_article_reader_result(reader_result: dict[str, Any], limit: int = 420) -> str:
    if reader_result.get("status") != "success":
        return ""
    return truncate_text(reader_result.get("text_excerpt") or reader_result.get("text"), limit)


def compact_article_reader_result(reader_result: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": normalize_text(reader_result.get("status")),
        "content_fetch_status": normalize_text(reader_result.get("content_fetch_status")),
        "content_quality": normalize_text(reader_result.get("content_quality")),
        "http_status": reader_result.get("http_status"),
        "error": normalize_text(reader_result.get("error")),
        "title": normalize_text(reader_result.get("title")),
        "char_count": reader_result.get("char_count"),
        "extraction_source": normalize_text(reader_result.get("extraction_source")),
        "reader_url": normalize_text(reader_result.get("reader_url")),
        "jina_reader_error": normalize_text(reader_result.get("jina_reader_error")),
        "extractor_version": normalize_text(reader_result.get("extractor_version")),
        "fetched_at": normalize_text(reader_result.get("fetched_at")),
        "from_cache": bool(reader_result.get("from_cache")),
        "cache_file": normalize_text(reader_result.get("cache_file")),
    }
