# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import html
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv
from zoneinfo import ZoneInfo


class JSONExtractionError(ValueError):
    pass


class JSONParsingError(ValueError):
    pass


TIMEZONE = "Asia/Shanghai"
SUPPORTED_SOURCE_TYPES = ("newsnow", "rss")
SUPPORTED_WRITER_MODES = ("news", "expert")
DEFAULT_WRITER_MODE = "expert"

CONTEXT_REPORT_DIR = "data/analysis/context"
BASIC_REPORT_DIR = "data/analysis/reports"
EXPERT_REPORT_DIR = "data/analysis/expert_reports"
LLM_REPORT_DIR = "data/analysis/llm_reports"

CONTEXT_FILE_PATTERNS = {
    "newsnow": "newsnow_cluster_context_*.json",
    "rss": "rss_cluster_context_*.json",
}
BASIC_FILE_PATTERNS = {
    "newsnow": "newsnow_basic_analysis_*.json",
    "rss": "rss_basic_analysis_*.json",
}
EXPERT_FILE_PATTERNS = {
    "newsnow": "newsnow_expert_report_*.json",
    "rss": "rss_expert_report_*.json",
}

LLM_API_KEY_ENV = "LLM_EXPERT_WRITER_API_KEY"
LLM_BASE_URL_ENV = "LLM_EXPERT_WRITER_BASE_URL"
LLM_MODEL_ENV = "LLM_EXPERT_WRITER_MODEL"
LLM_TIMEOUT_ENV = "LLM_EXPERT_WRITER_TIMEOUT"
LLM_TEMPERATURE_ENV = "LLM_EXPERT_WRITER_TEMPERATURE"
DEFAULT_LLM_BASE_URL = ""
DEFAULT_TIMEOUT = 90
DEFAULT_TEMPERATURE = 0.35

STRUCTURAL_FIELDS = (
    "cluster_id",
    "rank",
    "event_title",
    "event_type",
    "effective_event_type",
    "heat_score",
    "podcast_candidate_score",
)
NEWS_WRITABLE_FIELDS = (
    "final_summary",
    "source_grounded_summary",
    "what_happened",
    "source_breakdown",
    "known_facts",
    "uncertainties",
    "follow_up_points",
)
EXPERT_WRITABLE_FIELDS = (
    "final_summary",
    "expert_analysis",
    "why_it_really_matters",
    "key_risk",
    "uncertainty",
    "watch_points",
    "podcast_hook",
)
HIGH_RISK_PHRASES = (
    "国防生产法",
    "霍尔木兹",
    "中东能源通道",
    "国家安全例外",
    "军事升级",
    "封锁海峡",
)
ALLOWED_EQUIVALENT_TERMS = {
    "霍尔木兹": ("霍尔木兹", "hormuz", "strait of hormuz"),
    "hormuz": ("霍尔木兹", "hormuz", "strait of hormuz"),
    "strait of hormuz": ("霍尔木兹", "hormuz", "strait of hormuz"),
}
CONSERVATIVE_CUES = ("可能", "更像", "需要关注", "取决于", "不确定", "尚未确认")


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


load_dotenv(project_root() / ".env")


def now_dt() -> datetime:
    return datetime.now(ZoneInfo(TIMEZONE))


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def strip_html(value: Any) -> str:
    text = html.unescape(normalize_text(value))
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def truncate_text(text: str, limit: int = 500) -> str:
    clean = normalize_text(text)
    if len(clean) <= limit:
        return clean
    return clean[: limit - 1].rstrip() + "…"


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def infer_source_type_from_payload(payload: dict[str, Any]) -> str:
    source_type = normalize_text(payload.get("source_type"))
    if source_type not in SUPPORTED_SOURCE_TYPES:
        raise ValueError("Unable to infer source_type from input file.")
    return source_type


def find_latest_file(base_dir: Path, directory: str, pattern: str) -> Path:
    candidates = list((base_dir / directory).glob(pattern))
    if not candidates:
        raise FileNotFoundError(f"No file found for {directory}/{pattern}")
    return max(candidates, key=lambda path: path.stat().st_mtime)


def find_latest_context_report(base_dir: Path, source_type: str) -> Path:
    return find_latest_file(base_dir, CONTEXT_REPORT_DIR, CONTEXT_FILE_PATTERNS[source_type])


def find_latest_basic_report(base_dir: Path, source_type: str) -> Path | None:
    try:
        return find_latest_file(base_dir, BASIC_REPORT_DIR, BASIC_FILE_PATTERNS[source_type])
    except FileNotFoundError:
        return None


def find_latest_expert_report(base_dir: Path, source_type: str) -> Path:
    return find_latest_file(base_dir, EXPERT_REPORT_DIR, EXPERT_FILE_PATTERNS[source_type])


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate final LLM writing from pipeline reports.")
    parser.add_argument(
        "--mode",
        choices=SUPPORTED_WRITER_MODES,
        default=DEFAULT_WRITER_MODE,
        help="news=source-grounded news briefing, expert=expert-analysis writing.",
    )
    parser.add_argument("--source-type", choices=SUPPORTED_SOURCE_TYPES, help="Only process the specified source type.")
    parser.add_argument("--input-file", help="Explicit context JSON in news mode, or expert report JSON in expert mode.")
    return parser.parse_args()


def resolve_targets(args: argparse.Namespace, base_dir: Path) -> list[tuple[str, Path, dict[str, Any]]]:
    if args.input_file:
        input_path = Path(args.input_file).resolve()
        payload = load_json(input_path)
        inferred_source_type = infer_source_type_from_payload(payload)
        if args.source_type and inferred_source_type != args.source_type:
            raise ValueError(
                f"--source-type {args.source_type} does not match input file source_type {inferred_source_type}"
            )
        return [(inferred_source_type, input_path, payload)]

    source_types = [args.source_type] if args.source_type else list(SUPPORTED_SOURCE_TYPES)
    targets: list[tuple[str, Path, dict[str, Any]]] = []
    for source_type in source_types:
        if args.mode == "news":
            path = find_latest_context_report(base_dir, source_type)
        else:
            path = find_latest_expert_report(base_dir, source_type)
        targets.append((source_type, path, load_json(path)))
    return targets


def compact_list(items: list[Any], limit: int = 3) -> list[str]:
    result: list[str] = []
    for item in items[:limit]:
        text = normalize_text(item)
        if text:
            result.append(text)
    return result


def parse_env_int(name: str, default: int) -> int:
    raw_value = normalize_text(os.getenv(name))
    if not raw_value:
        return default
    try:
        return int(raw_value)
    except ValueError:
        return default


def parse_env_float(name: str, default: float) -> float:
    raw_value = normalize_text(os.getenv(name))
    if not raw_value:
        return default
    try:
        return float(raw_value)
    except ValueError:
        return default


def llm_config() -> dict[str, Any] | None:
    api_key = normalize_text(os.getenv(LLM_API_KEY_ENV))
    base_url = normalize_text(os.getenv(LLM_BASE_URL_ENV) or DEFAULT_LLM_BASE_URL).rstrip("/")
    model = normalize_text(os.getenv(LLM_MODEL_ENV))
    if not api_key or not base_url or not model:
        return None
    return {
        "api_key": api_key,
        "base_url": base_url,
        "model": model,
        "timeout": parse_env_int(LLM_TIMEOUT_ENV, DEFAULT_TIMEOUT),
        "temperature": parse_env_float(LLM_TEMPERATURE_ENV, DEFAULT_TEMPERATURE),
    }


def extract_message_content(choice: dict[str, Any]) -> str:
    message = choice.get("message") or {}
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        pieces: list[str] = []
        for part in content:
            if isinstance(part, str):
                pieces.append(part)
            elif isinstance(part, dict):
                text = part.get("text") or part.get("content")
                if isinstance(text, str):
                    pieces.append(text)
        return "\n".join(pieces)
    return ""


def extract_json_slice(text: str) -> str:
    clean = normalize_text(text)
    clean = re.sub(r"^```(?:json)?", "", clean, flags=re.IGNORECASE).strip()
    clean = re.sub(r"```$", "", clean).strip()
    start = clean.find("{")
    end = clean.rfind("}")
    if start < 0 or end < start:
        raise JSONExtractionError("No complete JSON object found in LLM response.")
    return clean[start : end + 1]


def remove_trailing_commas(text: str) -> str:
    return re.sub(r",(\s*[}\]])", r"\1", text)


def extract_json_object(text: str) -> dict[str, Any]:
    extracted = extract_json_slice(text)
    try:
        return json.loads(extracted)
    except json.JSONDecodeError:
        pass
    repaired = remove_trailing_commas(extracted)
    try:
        return json.loads(repaired)
    except json.JSONDecodeError as exc:
        raise JSONParsingError(f"JSON parsing failed after extraction: {exc.msg}") from exc


def call_llm(prompt: str, mode: str) -> dict[str, Any]:
    config = llm_config()
    if not config:
        raise RuntimeError("LLM configuration is not available.")

    system_content = (
        "你是严谨的新闻事实整理员。只能基于输入新闻源改写和归纳，不加入专家判断、动机推断或未证实细节。"
        if mode == "news"
        else "你是严谨、克制的专家分析编辑。禁止把推断写成事实，禁止补充输入中没有的具体机制或政策细节。"
    )
    response = requests.post(
        f"{config['base_url']}/chat/completions",
        headers={
            "Authorization": f"Bearer {config['api_key']}",
            "Content-Type": "application/json",
        },
        json={
            "model": config["model"],
            "messages": [
                {"role": "system", "content": system_content},
                {"role": "user", "content": prompt},
            ],
            "temperature": config["temperature"],
            "stream": False,
        },
        timeout=config["timeout"],
    )
    response.raise_for_status()
    payload = response.json()
    choices = payload.get("choices") or []
    if not choices:
        raise RuntimeError("LLM returned no choices.")
    content = extract_message_content(choices[0] or {})
    if not content:
        raise RuntimeError("LLM returned empty message content.")
    return extract_json_object(content)


def source_distribution(articles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for article in articles:
        source_name = normalize_text(article.get("source_name") or article.get("source_id") or "unknown")
        counts[source_name] = counts.get(source_name, 0) + 1
    return [
        {"source_name": source_name, "article_count": count}
        for source_name, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ]


def basic_analysis_map(base_dir: Path, source_type: str) -> dict[str, dict[str, Any]]:
    path = find_latest_basic_report(base_dir, source_type)
    if path is None:
        return {}
    try:
        payload = load_json(path)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[WARN] Unable to read basic report {path}: {exc}")
        return {}
    return {
        normalize_text(item.get("cluster_id")): item
        for item in payload.get("analyses", [])
        if normalize_text(item.get("cluster_id"))
    }


def build_news_item(context: dict[str, Any], basic: dict[str, Any] | None = None) -> dict[str, Any]:
    basic = basic or {}
    articles = context.get("articles", []) if isinstance(context.get("articles"), list) else []
    event_type = normalize_text(basic.get("event_type") or context.get("event_type") or "news")
    return {
        "cluster_id": normalize_text(context.get("cluster_id")),
        "rank": context.get("rank"),
        "event_title": normalize_text(context.get("event_title")),
        "event_type": event_type,
        "effective_event_type": event_type,
        "heat_score": context.get("heat_score"),
        "podcast_candidate_score": basic.get("podcast_candidate_score"),
        "summary": normalize_text(basic.get("summary")),
        "key_facts": compact_list(basic.get("key_facts", []), limit=8),
        "watch_points": compact_list(basic.get("watch_points", []), limit=5),
        "source_quality": basic.get("source_quality"),
        "representative_titles": compact_list(context.get("representative_titles", []), limit=8),
        "sources": compact_list(context.get("sources", []), limit=50),
        "source_distribution": source_distribution(articles),
        "articles": articles,
        "timeline": context.get("timeline", []),
        "context_stats": context.get("context_stats", {}),
        "total_articles": context.get("total_articles") or len(articles),
        "unique_sources": context.get("unique_sources") or len(source_distribution(articles)),
    }


def article_prompt_records(articles: list[dict[str, Any]], limit: int = 16) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for article in articles[:limit]:
        records.append(
            {
                "source_name": normalize_text(article.get("source_name")),
                "title": normalize_text(article.get("title")),
                "url": normalize_text(article.get("url")),
                "published_at": normalize_text(article.get("published_at") or article.get("resolved_time")),
                "summary": truncate_text(strip_html(article.get("summary") or article.get("content")), limit=420),
            }
        )
    return records


def build_news_prompt(item: dict[str, Any]) -> str:
    prompt_payload = {
        "event_title": item.get("event_title"),
        "heat_score": item.get("heat_score"),
        "total_articles": item.get("total_articles"),
        "unique_sources": item.get("unique_sources"),
        "sources": item.get("sources"),
        "source_distribution": item.get("source_distribution"),
        "basic_summary": item.get("summary"),
        "key_facts": item.get("key_facts"),
        "watch_points": item.get("watch_points"),
        "representative_titles": item.get("representative_titles"),
        "articles": article_prompt_records(item.get("articles", []), limit=16),
    }
    return f"""你是新闻事实整理员，不是评论员。请只根据输入里的新闻标题、摘要、来源和时间信息，整理一个客观新闻稿。

要求：
1. 只输出 JSON 对象，不要 markdown，不要解释。
2. 不要加入专家视角、投资判断、地缘战略判断、动机推断、政策推演或输入中没有的新事实。
3. 没有被来源明确确认的内容，必须写成“尚未确认”“报道提到”“部分来源称”。
4. final_summary 用 1 句话概括事实进展。
5. source_grounded_summary 用 1-2 段说明各来源共同确认了什么、分歧或不完整处是什么。
6. what_happened 用 2-4 个短句列出已发生事项。
7. source_breakdown 按媒体源简述数量和主要侧重。
8. known_facts 列出 3-6 条事实。
9. uncertainties 列出 1-4 条仍不确定或需要补充的信息。
10. follow_up_points 列出 1-3 条后续可关注事项，保持事实跟踪口吻。

只允许输出这些字段：
final_summary
source_grounded_summary
what_happened
source_breakdown
known_facts
uncertainties
follow_up_points

输入数据：
{json.dumps(prompt_payload, ensure_ascii=False, indent=2)}
"""


def build_expert_prompt(report: dict[str, Any]) -> str:
    expert_context_used = report.get("expert_context_used", [])
    expert_context_lines = []
    for item in expert_context_used[:3]:
        expert_context_lines.append(
            f"- {normalize_text(item.get('title'))} | domain={normalize_text(item.get('domain'))} | score={item.get('score')}"
        )
    expert_context_block = "\n".join(expert_context_lines) if expert_context_lines else "- none"

    prompt_payload = {
        "event_title": normalize_text(report.get("event_title")),
        "event_type": normalize_text(report.get("event_type")),
        "effective_event_type": normalize_text(report.get("effective_event_type")),
        "heat_score": report.get("heat_score"),
        "event_summary": normalize_text(report.get("event_summary")),
        "expert_lens": normalize_text(report.get("expert_lens")),
        "core_judgment": normalize_text(report.get("core_judgment")),
        "why_it_matters": normalize_text(report.get("why_it_matters")),
        "key_facts": compact_list(report.get("key_facts", []), limit=5),
        "expert_context_used": expert_context_block,
        "risk_assessment": normalize_text(report.get("risk_assessment")),
        "market_impact": normalize_text(report.get("market_impact")),
        "geopolitical_impact": normalize_text(report.get("geopolitical_impact")),
        "watch_points": compact_list(report.get("watch_points", []), limit=5),
        "uncertainty": normalize_text(report.get("uncertainty")),
        "podcast_angle": normalize_text(report.get("podcast_angle")),
        "podcast_candidate_score": report.get("podcast_candidate_score"),
    }

    return f"""你是资深新闻、市场和地缘分析编辑。请基于下面的结构化专家分析输入，生成更自然的专家分析稿。

要求：
1. 只输出 JSON 对象，不要 markdown，不要解释。
2. 可以使用专家视角，但所有判断都必须能被输入里的事实、专家 lens 或 retrieved context 支撑。
3. 不要创造输入中没有的具体事实、数字、机制、政策工具或行动计划。
4. 推断必须用保守表达，例如“可能”“更像”“需要关注”“取决于”“尚未确认”。
5. final_summary 要短，expert_analysis 写 1-2 段，why_it_really_matters 写清重要性，key_risk 只保留最关键一条。
6. watch_points 压缩成最重要的 3 条，podcast_hook 要适合作为播客开场。

只允许输出这些字段：
final_summary
expert_analysis
why_it_really_matters
key_risk
uncertainty
watch_points
podcast_hook

输入数据：
{json.dumps(prompt_payload, ensure_ascii=False, indent=2)}
"""


def build_news_supporting_text(item: dict[str, Any]) -> str:
    pieces = [
        normalize_text(item.get("event_title")),
        normalize_text(item.get("summary")),
    ]
    pieces.extend(compact_list(item.get("representative_titles", []), limit=20))
    pieces.extend(compact_list(item.get("key_facts", []), limit=10))
    pieces.extend(compact_list(item.get("watch_points", []), limit=10))
    for article in item.get("articles", [])[:30]:
        pieces.append(normalize_text(article.get("title")))
        pieces.append(strip_html(article.get("summary") or article.get("content")))
    return "\n".join(piece for piece in pieces if piece)


def build_expert_supporting_text(report: dict[str, Any]) -> str:
    pieces = [
        normalize_text(report.get("event_title")),
        normalize_text(report.get("event_summary")),
        normalize_text(report.get("expert_lens")),
        normalize_text(report.get("core_judgment")),
        normalize_text(report.get("why_it_matters")),
        normalize_text(report.get("risk_assessment")),
        normalize_text(report.get("market_impact")),
        normalize_text(report.get("geopolitical_impact")),
        normalize_text(report.get("uncertainty")),
        normalize_text(report.get("podcast_angle")),
    ]
    pieces.extend(compact_list(report.get("key_facts", []), limit=8))
    pieces.extend(compact_list(report.get("watch_points", []), limit=6))
    for item in report.get("expert_context_used", [])[:5]:
        pieces.append(normalize_text(item.get("title")))
        pieces.append(normalize_text(item.get("domain")))
    return "\n".join(piece for piece in pieces if piece)


def combined_generated_text(generated: dict[str, Any], fields: tuple[str, ...]) -> str:
    pieces: list[str] = []
    for key in fields:
        value = generated.get(key)
        if isinstance(value, list):
            pieces.extend(normalize_text(item) for item in value if normalize_text(item))
        else:
            text = normalize_text(value)
            if text:
                pieces.append(text)
    return "\n".join(pieces)


def term_supported(phrase: str, support_text: str) -> bool:
    phrase_lower = phrase.lower()
    if phrase_lower in support_text:
        return True
    for equivalent in ALLOWED_EQUIVALENT_TERMS.get(phrase_lower, ()):
        if equivalent.lower() in support_text:
            return True
    for equivalents in ALLOWED_EQUIVALENT_TERMS.values():
        lowered = tuple(item.lower() for item in equivalents)
        if phrase_lower in lowered:
            return any(item in support_text for item in lowered)
    return False


def unsupported_high_risk_phrases(
    supporting_text: str,
    generated: dict[str, Any],
    fields: tuple[str, ...],
) -> list[str]:
    support_text = supporting_text.lower()
    output_text = combined_generated_text(generated, fields).lower()
    unsupported: list[str] = []
    for phrase in HIGH_RISK_PHRASES:
        phrase_lower = phrase.lower()
        if phrase_lower in output_text and not term_supported(phrase_lower, support_text):
            unsupported.append(phrase)
    return unsupported


def lacks_conservative_language(generated: dict[str, Any]) -> bool:
    output_text = combined_generated_text(generated, EXPERT_WRITABLE_FIELDS)
    return not any(cue in output_text for cue in CONSERVATIVE_CUES)


def guardrail_allows_news_output(item: dict[str, Any], generated: dict[str, Any]) -> tuple[bool, str]:
    unsupported = unsupported_high_risk_phrases(build_news_supporting_text(item), generated, NEWS_WRITABLE_FIELDS)
    if unsupported:
        return False, f"Unsupported high-risk phrases: {', '.join(unsupported)}"
    output_text = combined_generated_text(generated, NEWS_WRITABLE_FIELDS)
    expert_markers = ("专家认为", "从专家角度", "投资者应", "市场应该定价", "战略意图是")
    found_markers = [marker for marker in expert_markers if marker in output_text]
    if found_markers:
        return False, f"News mode contains expert-style markers: {', '.join(found_markers)}"
    return True, ""


def guardrail_allows_expert_output(report: dict[str, Any], generated: dict[str, Any]) -> tuple[bool, str]:
    unsupported = unsupported_high_risk_phrases(build_expert_supporting_text(report), generated, EXPERT_WRITABLE_FIELDS)
    if unsupported:
        return False, f"Unsupported high-risk phrases: {', '.join(unsupported)}"
    effective_event_type = normalize_text(report.get("effective_event_type"))
    if effective_event_type == "geopolitics" and lacks_conservative_language(generated):
        return False, "Geopolitics output lacks conservative wording."
    return True, ""


def fallback_news_summary(item: dict[str, Any]) -> str:
    summary = normalize_text(item.get("summary"))
    if summary:
        return summary
    titles = compact_list(item.get("representative_titles", []), limit=2)
    if titles:
        return "；".join(titles)
    return normalize_text(item.get("event_title"))


def build_news_fallback_output(item: dict[str, Any]) -> dict[str, Any]:
    articles = item.get("articles", []) if isinstance(item.get("articles"), list) else []
    facts = compact_list(item.get("key_facts", []), limit=6)
    if not facts:
        facts = compact_list([article.get("title") for article in articles], limit=5)
    source_breakdown = [
        f"{entry['source_name']}：{entry['article_count']} 条"
        for entry in item.get("source_distribution", [])[:8]
        if entry.get("source_name")
    ]
    output = {
        "cluster_id": normalize_text(item.get("cluster_id")),
        "rank": item.get("rank"),
        "event_title": normalize_text(item.get("event_title")),
        "event_type": normalize_text(item.get("event_type") or "news"),
        "effective_event_type": normalize_text(item.get("effective_event_type") or item.get("event_type") or "news"),
        "heat_score": item.get("heat_score"),
        "final_summary": fallback_news_summary(item),
        "source_grounded_summary": fallback_news_summary(item),
        "what_happened": compact_list([article.get("title") for article in articles], limit=4),
        "source_breakdown": source_breakdown,
        "known_facts": facts,
        "uncertainties": ["当前仅基于本地已采集的标题、摘要和上下文整理；未抓到全文的来源可能存在信息缺口。"],
        "follow_up_points": compact_list(item.get("watch_points", []), limit=3),
        "podcast_candidate_score": item.get("podcast_candidate_score"),
    }
    output["expert_analysis"] = ""
    output["why_it_really_matters"] = ""
    output["key_risk"] = ""
    output["uncertainty"] = "普通新闻事实整理模式未加入专家判断。"
    output["watch_points"] = output["follow_up_points"]
    output["podcast_hook"] = ""
    return output


def fallback_expert_final_summary(report: dict[str, Any]) -> str:
    event_title = normalize_text(report.get("event_title"))
    core_judgment = normalize_text(report.get("core_judgment"))
    if core_judgment:
        return core_judgment
    return event_title


def build_expert_fallback_output(report: dict[str, Any]) -> dict[str, Any]:
    event_summary = normalize_text(report.get("event_summary"))
    core_judgment = normalize_text(report.get("core_judgment"))
    expert_lens = normalize_text(report.get("expert_lens"))
    risk_assessment = normalize_text(report.get("risk_assessment"))
    why_it_matters = normalize_text(report.get("why_it_matters"))
    market_impact = normalize_text(report.get("market_impact"))
    geopolitical_impact = normalize_text(report.get("geopolitical_impact"))
    return {
        "cluster_id": normalize_text(report.get("cluster_id")),
        "rank": report.get("rank"),
        "event_title": normalize_text(report.get("event_title")),
        "event_type": normalize_text(report.get("event_type")),
        "effective_event_type": normalize_text(report.get("effective_event_type") or report.get("event_type")),
        "heat_score": report.get("heat_score"),
        "final_summary": fallback_expert_final_summary(report),
        "expert_analysis": " ".join(piece for piece in [event_summary, core_judgment, expert_lens, risk_assessment] if piece),
        "why_it_really_matters": " ".join(
            piece for piece in [why_it_matters, market_impact, geopolitical_impact] if piece
        ),
        "key_risk": risk_assessment or normalize_text(report.get("uncertainty")),
        "uncertainty": normalize_text(report.get("uncertainty")),
        "watch_points": compact_list(report.get("watch_points", []), limit=3),
        "podcast_hook": normalize_text(report.get("podcast_angle")),
        "podcast_candidate_score": report.get("podcast_candidate_score"),
    }


def finalize_news_output(item: dict[str, Any], generated: dict[str, Any]) -> dict[str, Any]:
    fallback = build_news_fallback_output(item)
    output: dict[str, Any] = {key: fallback.get(key) for key in STRUCTURAL_FIELDS}
    for key in NEWS_WRITABLE_FIELDS:
        fallback_value = fallback[key]
        value = generated.get(key, fallback_value)
        if key in {"what_happened", "source_breakdown", "known_facts", "uncertainties", "follow_up_points"}:
            output[key] = compact_list(value if isinstance(value, list) else fallback_value, limit=8)
            continue
        output[key] = value if normalize_text(value) else fallback_value

    output["expert_analysis"] = ""
    output["why_it_really_matters"] = ""
    output["key_risk"] = ""
    output["uncertainty"] = "普通新闻事实整理模式未加入专家判断。"
    output["watch_points"] = output["follow_up_points"]
    output["podcast_hook"] = ""
    return output


def finalize_expert_output(report: dict[str, Any], generated: dict[str, Any]) -> dict[str, Any]:
    fallback = build_expert_fallback_output(report)
    output: dict[str, Any] = {key: fallback.get(key) for key in STRUCTURAL_FIELDS}
    for key in EXPERT_WRITABLE_FIELDS:
        fallback_value = fallback[key]
        value = generated.get(key, fallback_value)
        if key == "watch_points":
            output[key] = compact_list(value if isinstance(value, list) else fallback_value, limit=3)
            continue
        output[key] = value if normalize_text(value) or isinstance(value, (int, float, list)) else fallback_value
    return output


def generate_news_item(item: dict[str, Any]) -> tuple[dict[str, Any], str]:
    prompt = build_news_prompt(item)
    cluster_id = normalize_text(item.get("cluster_id"))
    try:
        generated = call_llm(prompt, mode="news")
        allowed, reason = guardrail_allows_news_output(item, generated)
        if not allowed:
            raise RuntimeError(f"Guardrail triggered: {reason}")
        print(f"[INFO] Generated news writing with llm for cluster_id={cluster_id}")
        return finalize_news_output(item, generated), "llm"
    except Exception as exc:
        print(f"[WARN] Falling back for cluster_id={cluster_id}: {type(exc).__name__}: {exc}")
        return build_news_fallback_output(item), "fallback"


def generate_expert_item(report: dict[str, Any]) -> tuple[dict[str, Any], str]:
    prompt = build_expert_prompt(report)
    cluster_id = normalize_text(report.get("cluster_id"))
    try:
        generated = call_llm(prompt, mode="expert")
        allowed, reason = guardrail_allows_expert_output(report, generated)
        if not allowed:
            raise RuntimeError(f"Guardrail triggered: {reason}")
        print(f"[INFO] Generated expert writing with llm for cluster_id={cluster_id}")
        return finalize_expert_output(report, generated), "llm"
    except Exception as exc:
        print(f"[WARN] Falling back for cluster_id={cluster_id}: {type(exc).__name__}: {exc}")
        return build_expert_fallback_output(report), "fallback"


def build_output_path(base_dir: Path, source_type: str, generated_at: datetime) -> Path:
    output_dir = base_dir / LLM_REPORT_DIR
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir / f"{source_type}_llm_report_{generated_at.strftime('%Y-%m-%d_%H')}.json"


def build_news_items(base_dir: Path, source_type: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
    basic_by_cluster = basic_analysis_map(base_dir, source_type)
    return [
        build_news_item(context, basic_by_cluster.get(normalize_text(context.get("cluster_id"))))
        for context in payload.get("contexts", [])
    ]


def build_llm_report_for_source_type(
    base_dir: Path,
    source_type: str,
    input_file: Path,
    payload: dict[str, Any],
    mode: str,
) -> Path:
    config = llm_config()
    print(f"[INFO] source_type={source_type}")
    print(f"[INFO] writer_mode={mode}")
    print(f"[INFO] LLM configuration detected: {'yes' if config else 'no'}")

    source_items = build_news_items(base_dir, source_type, payload) if mode == "news" else payload.get("reports", [])
    items = []
    llm_count = 0
    for source_item in source_items:
        generated_item, generation_mode = (
            generate_news_item(source_item) if mode == "news" else generate_expert_item(source_item)
        )
        if generation_mode == "llm":
            llm_count += 1
        items.append(generated_item)

    generated_at = now_dt()
    generation_mode = "llm" if items and llm_count == len(items) else "fallback"
    output_payload = {
        "generated_at": generated_at.strftime("%Y-%m-%d %H:%M:%S %z"),
        "source_type": source_type,
        "writer_mode": mode,
        "report_count": len(items),
        "generation_mode": generation_mode,
        "items": items,
    }
    if mode == "news":
        output_payload["source_context_file"] = str(input_file)
    else:
        output_payload["source_expert_report_file"] = str(input_file)

    output_path = build_output_path(base_dir, source_type, generated_at)
    output_path.write_text(json.dumps(output_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[INFO] generation_mode={generation_mode} (llm_items={llm_count}/{len(items)})")
    return output_path


def main() -> None:
    args = parse_args()
    base_dir = project_root()
    targets = resolve_targets(args, base_dir)
    for source_type, input_path, payload in targets:
        output_path = build_llm_report_for_source_type(base_dir, source_type, input_path, payload, args.mode)
        print(f"[INFO] LLM report output: {output_path}")


if __name__ == "__main__":
    main()
