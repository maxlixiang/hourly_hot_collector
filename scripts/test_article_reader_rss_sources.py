# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import html
import json
import random
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any
import xml.etree.ElementTree as ET

import requests


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.collectors.collector_common import RSS_HEADERS, normalize_text
from app.tools.article_reader import compact_article_reader_result, read_article
from app.tools.article_reader.reader import (
    USER_AGENT,
    extract_with_trafilatura,
    failed_result,
    success_result,
)


RSS_SOURCES = [
    ("HuffPost World News", "https://www.huffpost.com/section/world-news/feed"),
    ("Los Angeles Times World Nation", "https://www.latimes.com/world-nation/rss2.0.xml"),
    ("CNN Edition", "http://rss.cnn.com/rss/edition.rss"),
    ("Yahoo Most Viewed", "https://news.yahoo.com/rss/mostviewed"),
    ("CNBC World News", "https://www.cnbc.com/id/100003114/device/rss/rss.html"),
    ("Fox News World", "https://moxie.foxnews.com/google-publisher/world.xml"),
]

GLOBAL_RSS_SOURCES = [
    ("BBC World", "https://feeds.bbci.co.uk/news/world/rss.xml"),
    ("NYT World", "https://rss.nytimes.com/services/xml/rss/nyt/World.xml"),
    ("Al Jazeera", "https://www.aljazeera.com/xml/rss/all.xml"),
    ("NYT US News", "https://rss.nytimes.com/services/xml/rss/nyt/US.xml"),
    ("The Guardian World", "https://www.theguardian.com/world/rss"),
    ("Foreign Policy", "https://foreignpolicy.com/feed/"),
    ("The Diplomat", "https://thediplomat.com/feed/"),
]

TECH_FINANCE_RSS_SOURCES = [
    ("Ars Technica", "https://feeds.arstechnica.com/arstechnica/index"),
    ("The Verge", "https://www.theverge.com/rss/index.xml"),
    ("TechCrunch", "https://techcrunch.com/feed/"),
    ("Wired", "https://www.wired.com/feed/rss"),
    ("Engadget", "https://www.engadget.com/rss.xml"),
    ("The Github Blog", "https://github.blog/feed/"),
    ("MacRumors", "https://feeds.macrumors.com/MacRumors-All"),
    ("VentureBeat", "https://venturebeat.com/category/ai/feed/"),
    ("MIT Technology Review", "https://www.technologyreview.com/feed/"),
    ("Financial Times", "https://www.ft.com/rss/home"),
    ("MarketWatch", "https://feeds.marketwatch.com/marketwatch/topstories/"),
    ("Wall Street Journal Markets", "https://feeds.a.dj.com/rss/RSSMarketsMain.xml"),
    ("The Economist - Finance", "https://www.economist.com/finance-and-economics/rss.xml"),
]

RSS_SOURCE_SETS = {
    "default": RSS_SOURCES,
    "global": GLOBAL_RSS_SOURCES,
    "tech-finance": TECH_FINANCE_RSS_SOURCES,
}

RSS_BODY_ONLY_SOURCE_URLS = {
    "https://moxie.foxnews.com/google-publisher/world.xml",
}

MAX_REPORT_TEXT_CHARS = 6000


def strip_html(value: Any) -> str:
    text = html.unescape(normalize_text(value))
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def truncate_text(text: str, limit: int = MAX_REPORT_TEXT_CHARS) -> str:
    clean = normalize_text(text)
    if len(clean) <= limit:
        return clean
    return clean[: limit - 1].rstrip() + "..."


def text_excerpt(text: str, limit: int = 600) -> str:
    return truncate_text(text, limit)


def console_text(text: str) -> str:
    return text.encode("gbk", errors="replace").decode("gbk")


def infer_content_quality(text: str) -> str:
    clean_len = len(normalize_text(text))
    if clean_len >= 700:
        return "full_text"
    if clean_len >= 220:
        return "partial_text"
    if clean_len > 0:
        return "thin_text"
    return "none"


def element_text(parent: ET.Element, *names: str) -> str:
    for child in list(parent):
        local_name = child.tag.split("}", 1)[-1]
        if local_name in names:
            return "".join(child.itertext()).strip()
    return ""


def parse_rss_entries_with_content(xml_text: str) -> list[dict[str, str]]:
    root = ET.fromstring(xml_text)
    tag_name = root.tag.split("}", 1)[-1].lower()
    entries: list[dict[str, str]] = []

    if tag_name == "feed":
        nodes = root.findall(".//{*}entry")
    else:
        nodes = root.findall(".//item") or root.findall(".//{*}item")

    for node in nodes:
        link = ""
        if tag_name == "feed":
            for link_node in node.findall("{*}link"):
                href = normalize_text(link_node.attrib.get("href"))
                rel = normalize_text(link_node.attrib.get("rel")) or "alternate"
                if href and rel in {"alternate", ""}:
                    link = href
                    break
                if href and not link:
                    link = href
        else:
            link = element_text(node, "link")

        entries.append(
            {
                "title": element_text(node, "title"),
                "link": link,
                "pubDate": element_text(node, "pubDate", "date", "published", "updated"),
                "description": element_text(node, "description", "summary"),
                "content_encoded_html": element_text(node, "encoded"),
            }
        )

    return entries


def fetch_rss_entries(source_name: str, source_url: str, timeout: int) -> tuple[list[dict[str, str]], str]:
    try:
        response = requests.get(source_url, headers=RSS_HEADERS, timeout=timeout)
        response.raise_for_status()
        entries = parse_rss_entries_with_content(response.text)
        entries = [entry for entry in entries if normalize_text(entry.get("link"))]
        return entries, ""
    except Exception as exc:
        return [], f"{type(exc).__name__}: {exc}"


def read_article_with_trafilatura_only(url: str, timeout: int) -> dict[str, Any]:
    clean_url = normalize_text(url)
    if not clean_url:
        return failed_result("", "empty url")

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

    title, text = extract_with_trafilatura(response.text, clean_url)
    if not text:
        return failed_result(clean_url, "trafilatura extracted no readable article text", http_status)

    return success_result(
        url=clean_url,
        title=title,
        text=text,
        http_status=http_status,
        extraction_source="trafilatura_only",
    )


def source_stats(results: list[dict[str, Any]], result_key: str | None = None) -> dict[str, Any]:
    if result_key:
        rows = [item.get(result_key, {}) for item in results]
    else:
        rows = results

    total = len(rows)
    status_counts = Counter(normalize_text(item.get("status")) or "unknown" for item in rows)
    fetch_counts = Counter(normalize_text(item.get("content_fetch_status")) or "unknown" for item in rows)
    quality_counts = Counter(normalize_text(item.get("content_quality")) or "unknown" for item in rows)
    extractor_counts = Counter(normalize_text(item.get("extraction_source")) or "none" for item in rows)
    full_or_partial = sum(
        1
        for item in rows
        if normalize_text(item.get("content_quality")) in {"full_text", "partial_text"}
    )
    return {
        "sampled": total,
        "status_counts": dict(status_counts),
        "content_fetch_status_counts": dict(fetch_counts),
        "content_quality_counts": dict(quality_counts),
        "extraction_source_counts": dict(extractor_counts),
        "full_or_partial_rate": round(full_or_partial / total, 3) if total else 0,
    }


def report_payload(reader_result: dict[str, Any]) -> dict[str, Any]:
    compact = compact_article_reader_result(reader_result)
    return {
        **compact,
        "text": normalize_text(reader_result.get("text")),
        "text_excerpt": normalize_text(reader_result.get("text_excerpt")),
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    rng = random.Random(args.seed)
    report: dict[str, Any] = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "per_source": args.per_source,
        "seed": args.seed,
        "extractor_mode": args.extractor_mode,
        "source_set": args.source_set,
        "use_cache": not args.no_cache,
        "sources": [],
    }

    for source_name, source_url in RSS_SOURCE_SETS[args.source_set]:
        if args.skip_rss_body_only_sources and source_url in RSS_BODY_ONLY_SOURCE_URLS:
            print(f"[RSS] {source_name}: skipped rss-body-only source")
            continue
        print(f"[RSS] {source_name}: fetching feed")
        entries, feed_error = fetch_rss_entries(source_name, source_url, args.feed_timeout)
        sample_size = min(args.per_source, len(entries))
        sampled_entries = rng.sample(entries, sample_size) if sample_size else []

        source_results: list[dict[str, Any]] = []
        for index, entry in enumerate(sampled_entries, start=1):
            title = normalize_text(entry.get("title"))
            url = normalize_text(entry.get("link"))
            rss_content_html = normalize_text(entry.get("content_encoded_html"))
            rss_content_text = strip_html(rss_content_html)
            print(f"  [{index}/{sample_size}] reading: {console_text(title[:80] or url)}")

            if source_url in RSS_BODY_ONLY_SOURCE_URLS and rss_content_text:
                body_text = truncate_text(rss_content_text)
                reader_result = {
                    "status": "success",
                    "content_fetch_status": "success",
                    "content_quality": infer_content_quality(body_text),
                    "http_status": None,
                    "error": "",
                    "title": title,
                    "char_count": len(body_text),
                    "extraction_source": "rss_content_encoded",
                    "from_cache": False,
                    "text": body_text,
                    "text_excerpt": text_excerpt(body_text),
                }
            elif args.extractor_mode == "trafilatura":
                reader_result = read_article_with_trafilatura_only(url, args.article_timeout)
            elif args.extractor_mode == "compare":
                current_result = read_article(
                    url,
                    ROOT_DIR,
                    timeout=args.article_timeout,
                    use_cache=not args.no_cache,
                )
                trafilatura_result = read_article_with_trafilatura_only(url, args.article_timeout)
                source_results.append(
                    {
                        "title": title,
                        "url": url,
                        "pubDate": normalize_text(entry.get("pubDate")),
                        "rss_description_chars": len(normalize_text(entry.get("description"))),
                        "rss_content_encoded_chars": len(rss_content_text),
                        "rss_content_text": rss_content_text,
                        "current": report_payload(current_result),
                        "trafilatura": report_payload(trafilatura_result),
                    }
                )
                continue
            else:
                reader_result = read_article(
                    url,
                    ROOT_DIR,
                    timeout=args.article_timeout,
                    use_cache=not args.no_cache,
                )

            source_results.append(
                {
                    "title": title,
                    "url": url,
                    "pubDate": normalize_text(entry.get("pubDate")),
                    "rss_description_chars": len(normalize_text(entry.get("description"))),
                    "rss_content_encoded_chars": len(rss_content_text),
                    "rss_content_text": rss_content_text,
                    **report_payload(reader_result),
                }
            )

        if args.extractor_mode == "compare":
            stats = {
                "current": source_stats(source_results, "current"),
                "trafilatura": source_stats(source_results, "trafilatura"),
            }
        else:
            stats = source_stats(source_results)

        report["sources"].append(
            {
                "source_name": source_name,
                "source_url": source_url,
                "feed_item_count": len(entries),
                "feed_error": feed_error,
                "stats": stats,
                "items": source_results,
            }
        )

    return report


def print_summary(report: dict[str, Any]) -> None:
    print("\n=== article_reader RSS sample summary ===")
    for source in report["sources"]:
        stats = source["stats"]
        if "current" in stats and "trafilatura" in stats:
            current = stats["current"]
            trafilatura = stats["trafilatura"]
            print(
                f"- {source['source_name']}: feed_items={source['feed_item_count']} "
                f"sampled={current['sampled']} "
                f"current_rate={current['full_or_partial_rate']} current_extractor={current['extraction_source_counts']} "
                f"traf_rate={trafilatura['full_or_partial_rate']} traf_quality={trafilatura['content_quality_counts']}"
            )
        else:
            print(
                f"- {source['source_name']}: feed_items={source['feed_item_count']} "
                f"sampled={stats['sampled']} rate={stats['full_or_partial_rate']} "
                f"quality={stats['content_quality_counts']} extractor={stats['extraction_source_counts']}"
            )
        if source["feed_error"]:
            print(f"  feed_error={source['feed_error']}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Randomly sample RSS article URLs and test article_reader extraction.")
    parser.add_argument("--per-source", type=int, default=3, help="Number of articles to sample from each RSS feed.")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for reproducible sampling.")
    parser.add_argument("--feed-timeout", type=int, default=20, help="RSS feed request timeout in seconds.")
    parser.add_argument("--article-timeout", type=int, default=8, help="Direct article HTML request timeout in seconds.")
    parser.add_argument(
        "--source-set",
        choices=sorted(RSS_SOURCE_SETS),
        default="default",
        help="RSS source set to sample.",
    )
    parser.add_argument(
        "--extractor-mode",
        choices=["current", "trafilatura", "compare"],
        default="current",
        help="Extraction mode: current chain, trafilatura-only, or both on the same sampled URLs.",
    )
    parser.add_argument(
        "--skip-rss-body-only-sources",
        action="store_true",
        help="Skip sources that are meant to use RSS embedded body text, such as Fox News.",
    )
    parser.add_argument("--no-cache", action="store_true", help="Bypass article_reader success cache.")
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT_DIR / "data" / "agent" / "article_reader_rss_sample_report.json",
        help="JSON report output path.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    cli_args = parse_args()
    result = run(cli_args)
    cli_args.output.parent.mkdir(parents=True, exist_ok=True)
    cli_args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print_summary(result)
    print(f"\n[OK] Wrote report: {cli_args.output}")
