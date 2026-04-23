# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from zoneinfo import ZoneInfo


TIMEZONE = "Asia/Shanghai"
CONTEXT_DIR = "data/analysis/context"
REPORT_DIR = "data/analysis/reports"
SUPPORTED_SOURCE_TYPES = ("newsnow", "rss")
RULES_FILE = "config/basic_analysis_event_rules.txt"
CONTEXT_FILE_PREFIX_MAP = {
    "newsnow": "newsnow_cluster_context_*.json",
    "rss": "rss_cluster_context_*.json",
}
RULE_SECTION_MAP = {
    "GEOPOLITICS": "geopolitics",
    "MARKETS": "markets",
    "TECH": "tech",
    "RELIABLE_SOURCES": "reliable_sources",
}

_RULES_CACHE: dict[str, list[dict[str, Any]]] | None = None
_RULES_MTIME: float | None = None


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def load_basic_analysis_event_rules(base_dir: Path) -> dict[str, list[dict[str, Any]]]:
    global _RULES_CACHE
    global _RULES_MTIME

    config_path = base_dir / RULES_FILE
    if not config_path.exists():
        raise FileNotFoundError(f"Basic analysis rules file not found: {config_path}")

    current_mtime = config_path.stat().st_mtime
    if _RULES_CACHE is not None and _RULES_MTIME == current_mtime:
        return _RULES_CACHE

    parsed: dict[str, list[dict[str, Any]]] = {target_name: [] for target_name in RULE_SECTION_MAP.values()}
    current_section: str | None = None

    for raw_line in config_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section_name = line[1:-1].strip().upper()
            if section_name not in RULE_SECTION_MAP:
                raise ValueError(f"Unsupported section in {config_path}: {section_name}")
            current_section = RULE_SECTION_MAP[section_name]
            continue
        if current_section is None:
            raise ValueError(f"Value found before section declaration in {config_path}: {line}")

        if "=>" in line:
            pattern_text, alias = [part.strip() for part in line.split("=>", 1)]
        else:
            pattern_text, alias = line, line

        if pattern_text.startswith("/") and pattern_text.endswith("/i"):
            regex_body = pattern_text[1:-2]
            parsed[current_section].append(
                {
                    "type": "regex",
                    "pattern": regex_body,
                    "alias": alias,
                }
            )
        else:
            parsed[current_section].append(
                {
                    "type": "keyword",
                    "pattern": pattern_text,
                    "alias": alias,
                }
            )

    _RULES_CACHE = parsed
    _RULES_MTIME = current_mtime
    return parsed


def parse_datetime_object(value: str | None) -> datetime | None:
    text = normalize_text(value)
    if not text:
        return None
    try:
        parsed = datetime.strptime(text, "%Y-%m-%d %H:%M:%S %z")
    except ValueError:
        return None
    return parsed.astimezone(ZoneInfo(TIMEZONE))


def resolve_article_time(article: dict[str, Any]) -> datetime | None:
    published_at = parse_datetime_object(article.get("published_at"))
    if published_at is not None:
        return published_at
    return parse_datetime_object(article.get("fetched_at"))


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def find_latest_context_file(base_dir: Path, source_type: str) -> Path:
    pattern = CONTEXT_FILE_PREFIX_MAP[source_type]
    candidates = sorted((base_dir / CONTEXT_DIR).glob(pattern))
    if candidates:
        return candidates[-1]
    raise FileNotFoundError(f"No context file found for source_type={source_type}")


def load_context_payload(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def rule_matches(text: str, rule: dict[str, Any]) -> bool:
    if not text:
        return False
    if rule["type"] == "regex":
        return re.search(rule["pattern"], text, flags=re.IGNORECASE) is not None
    return rule["pattern"].lower() in text.lower()


def calculate_event_type_scores(text: str, rules: dict[str, list[dict[str, Any]]]) -> dict[str, int]:
    geopolitics_score = sum(1 for rule in rules["geopolitics"] if rule_matches(text, rule))
    markets_score = sum(1 for rule in rules["markets"] if rule_matches(text, rule))
    tech_score = sum(1 for rule in rules["tech"] if rule_matches(text, rule))
    return {
        "geopolitics": geopolitics_score,
        "markets": markets_score,
        "tech": tech_score,
    }


def detect_event_type(text: str, rules: dict[str, list[dict[str, Any]]]) -> str:
    scores = calculate_event_type_scores(text, rules)
    best_event_type = max(scores, key=scores.get)
    if scores[best_event_type] <= 0:
        return "society"
    if list(scores.values()).count(scores[best_event_type]) > 1:
        priority = ["geopolitics", "markets", "tech"]
        for event_type in priority:
            if scores[event_type] == scores[best_event_type]:
                return event_type
        return "society"
    return best_event_type


def build_summary(context: dict[str, Any]) -> str:
    event_title = normalize_text(context.get("event_title")) or "该热点事件"
    stats = context.get("context_stats", {})
    time_span_hours = stats.get("time_span_hours", 0.0)
    article_count = stats.get("article_count", 0)
    source_count = stats.get("source_count", 0)
    representative_titles = [normalize_text(title) for title in context.get("representative_titles", []) if normalize_text(title)]
    title_part = "；".join(representative_titles[:3]) if representative_titles else event_title
    return (
        f"该事件围绕“{event_title}”展开。过去 {time_span_hours} 小时内，"
        f"共出现 {article_count} 条相关新闻，来自 {source_count} 个来源。"
        f"主要标题包括：{title_part}。"
    )


def build_why_it_matters(event_type: str) -> str:
    if event_type == "geopolitics":
        return "该事件可能影响地缘政治风险、能源市场和金融市场情绪。"
    if event_type == "markets":
        return "该事件可能影响资本市场、行业预期或公司估值。"
    if event_type == "tech":
        return "该事件可能影响科技产业竞争格局或产品周期。"
    return "该事件因多来源集中报道，可能具有一定社会关注度。"


def build_key_facts(context: dict[str, Any]) -> list[str]:
    facts: list[str] = []
    timeline = context.get("timeline", [])
    representative_titles = context.get("representative_titles", [])

    if timeline:
        earliest = timeline[0]
        facts.append(
            f"最早信号出现在 {normalize_text(earliest.get('time'))}，标题为“{normalize_text(earliest.get('title'))}”。"
        )
        latest = timeline[-1]
        if latest != earliest:
            facts.append(
                f"最新进展出现在 {normalize_text(latest.get('time'))}，来源为 {normalize_text(latest.get('source_name'))}。"
            )

    for title in representative_titles[:3]:
        normalized = normalize_text(title)
        if normalized:
            facts.append(f"代表性标题包括：{normalized}")

    deduped_facts: list[str] = []
    seen: set[str] = set()
    for fact in facts:
        if fact not in seen:
            deduped_facts.append(fact)
            seen.add(fact)
        if len(deduped_facts) >= 5:
            break
    return deduped_facts


def build_possible_impacts(event_type: str) -> list[str]:
    if event_type == "geopolitics":
        return [
            "可能改变地区安全形势。",
            "可能影响能源价格或市场避险情绪。",
            "需要关注后续谈判和各方声明。",
        ]
    if event_type == "markets":
        return [
            "可能影响市场风险偏好。",
            "可能影响相关行业或资产价格。",
            "需要关注后续财报、政策或市场反应。",
        ]
    if event_type == "tech":
        return [
            "可能反映科技产业竞争变化。",
            "可能影响相关公司产品周期或产业链。",
            "需要关注技术落地和商业化进展。",
        ]
    return [
        "可能反映社会关注或公共议题变化。",
        "需要关注官方通报、后续调查或政策回应。",
        "后续舆论热度可能继续波动。",
    ]


def build_watch_points(event_type: str) -> list[str]:
    if event_type == "geopolitics":
        return [
            "后续是否有正式声明或谈判结果。",
            "是否出现军事行动或制裁升级。",
            "能源价格和市场避险情绪是否变化。",
        ]
    if event_type == "markets":
        return [
            "相关公司或板块是否出现连续市场反应。",
            "是否有进一步财报、公告或监管信息。",
            "资金流向和市场情绪是否延续。",
        ]
    if event_type == "tech":
        return [
            "产品或技术是否进入量产或商用阶段。",
            "竞争对手是否快速跟进。",
            "市场、用户或监管反馈如何。",
        ]
    return [
        "是否有官方调查或通报。",
        "舆论是否继续发酵。",
        "是否涉及政策、民生或公共安全。",
    ]


def count_reliable_sources(sources: list[str], reliable_source_rules: list[dict[str, Any]]) -> int:
    count = 0
    for source in sources:
        normalized_source = normalize_text(source)
        for reliable_source_rule in reliable_source_rules:
            if rule_matches(normalized_source, reliable_source_rule):
                count += 1
                break
    return count


def build_source_quality(
    context: dict[str, Any],
    reliable_source_rules: list[dict[str, Any]],
) -> dict[str, Any]:
    stats = context.get("context_stats", {})
    sources = [normalize_text(source) for source in context.get("sources", [])]
    reliable_source_count = count_reliable_sources(sources, reliable_source_rules)
    return {
        "source_count": stats.get("source_count", len(sources)),
        "article_count": stats.get("article_count", len(context.get("articles", []))),
        "has_multiple_reliable_sources": reliable_source_count >= 2,
    }


def build_podcast_candidate(context: dict[str, Any], event_type: str, source_quality: dict[str, Any]) -> tuple[int, str]:
    score = 0
    reasons: list[str] = []

    if int(source_quality.get("source_count", 0)) >= 5:
        score += 2
        reasons.append("来源数较多")
    if int(source_quality.get("article_count", 0)) >= 5:
        score += 2
        reasons.append("文章量较多")
    if float(context.get("heat_score", 0.0)) >= 8:
        score += 2
        reasons.append("热度较高")
    if event_type in {"geopolitics", "markets", "tech"}:
        score += 2
        reasons.append(f"主题属于 {event_type}")
    if bool(source_quality.get("has_multiple_reliable_sources")):
        score += 2
        reasons.append("有多个可靠来源")

    score = min(score, 10)
    if not reasons:
        reasons.append("事件影响面暂不明确")
    return score, "；".join(reasons)


def build_analysis(
    context: dict[str, Any],
    rules: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    joined_text = " ".join(
        [
            normalize_text(context.get("event_title")),
            " ".join(normalize_text(title) for title in context.get("representative_titles", [])),
            " ".join(normalize_text(article.get("title")) for article in context.get("articles", [])[:5]),
        ]
    )
    event_type = detect_event_type(joined_text, rules)
    source_quality = build_source_quality(context, rules["reliable_sources"])
    podcast_candidate_score, podcast_candidate_reason = build_podcast_candidate(
        context,
        event_type,
        source_quality,
    )

    return {
        "cluster_id": context.get("cluster_id"),
        "rank": context.get("rank"),
        "event_title": context.get("event_title"),
        "heat_score": context.get("heat_score"),
        "event_type": event_type,
        "summary": build_summary(context),
        "why_it_matters": build_why_it_matters(event_type),
        "key_facts": build_key_facts(context),
        "possible_impacts": build_possible_impacts(event_type),
        "watch_points": build_watch_points(event_type),
        "source_quality": source_quality,
        "podcast_candidate_score": podcast_candidate_score,
        "podcast_candidate_reason": podcast_candidate_reason,
    }


def build_output_path(base_dir: Path, source_type: str, generated_at: datetime) -> Path:
    output_dir = base_dir / REPORT_DIR
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir / f"{source_type}_basic_analysis_{generated_at.strftime('%Y-%m-%d_%H')}.json"


def build_analysis_for_source_type(base_dir: Path, source_type: str, input_file: Path | None = None) -> Path:
    rules = load_basic_analysis_event_rules(base_dir)
    context_file = input_file or find_latest_context_file(base_dir, source_type)
    payload = load_context_payload(context_file)
    analyses = [build_analysis(context, rules) for context in payload.get("contexts", [])]
    generated_at = datetime.now(ZoneInfo(TIMEZONE))
    output_payload = {
        "generated_at": generated_at.strftime("%Y-%m-%d %H:%M:%S %z"),
        "source_type": source_type,
        "source_context_file": str(context_file),
        "analysis_count": len(analyses),
        "analyses": analyses,
    }
    output_path = build_output_path(base_dir, source_type, generated_at)
    output_path.write_text(json.dumps(output_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build basic rule-based analysis reports from cluster context files.")
    parser.add_argument(
        "--source-type",
        choices=SUPPORTED_SOURCE_TYPES,
        help="Only build analysis for the specified source type.",
    )
    parser.add_argument(
        "--input-file",
        help="Use a specific cluster context JSON file instead of auto-detecting the latest one.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    base_dir = project_root()
    input_file = Path(args.input_file).resolve() if args.input_file else None

    if args.source_type and input_file is not None:
        payload = load_context_payload(input_file)
        inferred_source_type = normalize_text(payload.get("source_type"))
        if inferred_source_type != args.source_type:
            raise ValueError(
                f"--source-type {args.source_type} does not match input file source_type {inferred_source_type}"
            )
        source_types = [args.source_type]
    elif args.source_type:
        source_types = [args.source_type]
    elif input_file is not None:
        payload = load_context_payload(input_file)
        inferred_source_type = normalize_text(payload.get("source_type"))
        if inferred_source_type not in SUPPORTED_SOURCE_TYPES:
            raise ValueError("Unable to infer source_type from input context file.")
        source_types = [inferred_source_type]
    else:
        source_types = list(SUPPORTED_SOURCE_TYPES)

    for source_type in source_types:
        source_input_file = input_file if input_file is not None else None
        output_path = build_analysis_for_source_type(base_dir, source_type, source_input_file)
        print(f"[INFO] Basic analysis output: {output_path}")


if __name__ == "__main__":
    main()
