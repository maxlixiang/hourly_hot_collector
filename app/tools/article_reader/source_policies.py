# -*- coding: utf-8 -*-
from __future__ import annotations

import ast
import re
import unicodedata
from pathlib import Path
from typing import Any, Literal, TypedDict
from urllib.parse import urlparse


ArticleReadingPolicy = Literal["enabled", "rss_content", "disabled"]
DEFAULT_POLICY_FILE = "config/rss_source_policies.txt"


class SourcePolicy(TypedDict, total=False):
    article_reading: ArticleReadingPolicy | str
    reason: str
    source_url: str
    source_id: str


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def normalize_title(value: str) -> str:
    lowered = unicodedata.normalize("NFKC", normalize_text(value)).lower()
    lowered = re.sub(r"\s+", " ", lowered)
    lowered = re.sub(r"[^\w\s]", " ", lowered)
    lowered = re.sub(r"_", " ", lowered)
    return re.sub(r"\s+", " ", lowered).strip()


def slugify_text(value: str) -> str:
    normalized = normalize_title(value)
    slug = re.sub(r"\s+", "-", normalized)
    return slug[:200]


def source_slug_from_url(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.netloc.replace("www.", "")
    path = parsed.path.strip("/").replace("/", "-")
    base = f"{host}-{path}".strip("-")
    return slugify_text(base or url) or "rss-source"


def default_policy(source_url: str = "", source_id: str = "") -> SourcePolicy:
    return {
        "article_reading": "enabled",
        "reason": "",
        "source_url": normalize_text(source_url),
        "source_id": normalize_text(source_id),
    }


def load_source_policies(base_dir: Path) -> dict[str, SourcePolicy]:
    policy_path = base_dir / DEFAULT_POLICY_FILE
    if not policy_path.exists():
        return {}

    module = ast.parse(policy_path.read_text(encoding="utf-8"), filename=str(policy_path))
    raw_policies: dict[str, Any] = {}
    for node in module.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "RSS_SOURCE_POLICIES":
                    value = ast.literal_eval(node.value)
                    if isinstance(value, dict):
                        raw_policies = value
                    break

    policies: dict[str, SourcePolicy] = {}
    for source_url, raw_policy in raw_policies.items():
        clean_url = normalize_text(source_url)
        if not clean_url or not isinstance(raw_policy, dict):
            continue
        source_id = source_slug_from_url(clean_url)
        article_reading = normalize_text(raw_policy.get("article_reading")) or "enabled"
        if article_reading not in {"enabled", "rss_content", "disabled"}:
            article_reading = "enabled"
        policy: SourcePolicy = {
            "article_reading": article_reading,
            "reason": normalize_text(raw_policy.get("reason")),
            "source_url": clean_url,
            "source_id": source_id,
        }
        policies[clean_url] = policy
        policies[source_id] = policy
    return policies


def resolve_source_policy(
    base_dir: Path,
    *,
    source_url: str = "",
    source_id: str = "",
) -> SourcePolicy:
    policies = load_source_policies(base_dir)
    clean_url = normalize_text(source_url)
    clean_id = normalize_text(source_id)
    if clean_url and clean_url in policies:
        return policies[clean_url]
    if clean_id and clean_id in policies:
        return policies[clean_id]
    if clean_url:
        generated_id = source_slug_from_url(clean_url)
        if generated_id in policies:
            return policies[generated_id]
        return default_policy(clean_url, generated_id)
    return default_policy("", clean_id)
