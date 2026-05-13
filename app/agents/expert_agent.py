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
SUPPORTED_SOURCE_TYPES = ("newsnow", "rss")
CONTEXT_DIR = "data/analysis/context"
REPORT_DIR = "data/analysis/reports"
RETRIEVED_CONTEXT_DIR = "data/analysis/retrieved_context"
EXPERT_REPORT_DIR = "data/analysis/expert_reports"
FILE_PATTERNS = {
    "context": {
        "newsnow": "newsnow_cluster_context_*.json",
        "rss": "rss_cluster_context_*.json",
    },
    "analysis": {
        "newsnow": "newsnow_basic_analysis_*.json",
        "rss": "rss_basic_analysis_*.json",
    },
    "retrieved": {
        "newsnow": "newsnow_retrieved_context_*.json",
        "rss": "rss_retrieved_context_*.json",
    },
}
RELIABLE_SOURCES = {
    "财联社", "华尔街见闻 最热", "参考消息", "澎湃新闻 热榜", "腾讯新闻", "36Kr", "卫星通讯社",
    "NYT World", "The Guardian World", "Bloomberg Markets", "CNBC Business", "MarketWatch", "Al Jazeera", "Reuters",
}
EVENT_TYPE_OVERRIDE_KEYWORDS = {
    "geopolitics": {"ceasefire", "sanctions", "ukraine", "iran", "military", "strait"},
    "markets": {"deficit", "growth", "fiscal", "bond", "eurozone", "macro"},
    "tech": {"chip", "chips", "ai", "tpu", "gpu", "data center", "semiconductor"},
}
NO_KNOWLEDGE_LENS_BY_TYPE = {
    "geopolitics": "当前没有明显命中的专题知识卡片，这轮更适合先从安全信号、谈判路径和地区升级风险来理解事件。",
    "markets": "当前没有明显命中的专题知识卡片，这轮更适合先从风险偏好、估值变化和宏观约束来理解事件。",
    "tech": "当前没有明显命中的专题知识卡片，这轮更适合先从产业链、资本开支和生态竞争视角来理解事件。",
    "society": "当前没有明显命中的专题知识卡片，这轮更适合先从公共议题演化、信息确认和后续政策反应来理解事件。",
}


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def now_dt() -> datetime:
    return datetime.now(ZoneInfo(TIMEZONE))


def now_text() -> str:
    return now_dt().strftime("%Y-%m-%d %H:%M:%S %z")


def parse_dt(value: Any) -> datetime | None:
    text = normalize_text(value)
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S %z", "%Y-%m-%d %H:%M:%S"):
        try:
            parsed = datetime.strptime(text, fmt)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=ZoneInfo(TIMEZONE))
            return parsed.astimezone(ZoneInfo(TIMEZONE))
        except ValueError:
            continue
    return None


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def find_latest_file(base_dir: Path, file_type: str, source_type: str) -> Path:
    pattern = FILE_PATTERNS[file_type][source_type]
    directory_map = {
        "context": CONTEXT_DIR,
        "analysis": REPORT_DIR,
        "retrieved": RETRIEVED_CONTEXT_DIR,
    }
    candidates = sorted((base_dir / directory_map[file_type]).glob(pattern))
    if not candidates:
        raise FileNotFoundError(f"No {file_type} file found for source_type={source_type}")
    return candidates[-1]


def infer_source_type_from_payload(payload: dict[str, Any]) -> str:
    source_type = normalize_text(payload.get("source_type"))
    if source_type not in SUPPORTED_SOURCE_TYPES:
        raise ValueError("Unable to infer source_type from input file.")
    return source_type


def validate_explicit_file(path: Path, expected_source_type: str | None) -> tuple[dict[str, Any], str]:
    payload = load_json(path)
    inferred = infer_source_type_from_payload(payload)
    if expected_source_type and inferred != expected_source_type:
        raise ValueError(
            f"source_type mismatch: expected {expected_source_type}, got {inferred} from {path}"
        )
    return payload, inferred


def latest_or_explicit_payload(
    base_dir: Path,
    source_type: str | None,
    explicit_path: str | None,
    file_type: str,
) -> tuple[dict[str, Any], Path, str]:
    if explicit_path:
        path = Path(explicit_path).resolve()
        payload, inferred = validate_explicit_file(path, source_type)
        return payload, path, inferred
    if not source_type:
        raise ValueError(f"source_type is required when {file_type} file is not explicitly provided.")
    path = find_latest_file(base_dir, file_type, source_type)
    return load_json(path), path, source_type


def build_ranked_cluster_ids(
    analysis_payload: dict[str, Any],
    context_payload: dict[str, Any],
    retrieved_payload: dict[str, Any],
) -> list[str]:
    ordering: list[tuple[int, str]] = []
    seen: set[str] = set()
    for collection_key, rank_key in (("analyses", "rank"), ("contexts", "rank"), ("items", "rank")):
        payload = (
            analysis_payload if collection_key == "analyses"
            else context_payload if collection_key == "contexts"
            else retrieved_payload
        )
        for item in payload.get(collection_key, []):
            cluster_id = normalize_text(item.get("cluster_id"))
            if not cluster_id or cluster_id in seen:
                continue
            seen.add(cluster_id)
            ordering.append((int(item.get(rank_key) or 9999), cluster_id))
    ordering.sort(key=lambda item: (item[0], item[1]))
    return [cluster_id for _, cluster_id in ordering]


def index_by_cluster_id(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        normalize_text(item.get("cluster_id")): item
        for item in items
        if normalize_text(item.get("cluster_id"))
    }


def first_non_empty(*values: Any) -> str:
    for value in values:
        text = normalize_text(value)
        if text:
            return text
    return ""


def choose_event_title(context_item: dict[str, Any] | None, analysis_item: dict[str, Any] | None) -> str:
    return first_non_empty(
        context_item.get("event_title") if context_item else "",
        analysis_item.get("event_title") if analysis_item else "",
    )


def infer_time_span_hours(context_item: dict[str, Any] | None) -> float | None:
    if not context_item:
        return None
    stats = context_item.get("context_stats") or {}
    if stats.get("time_span_hours") is not None:
        try:
            return float(stats["time_span_hours"])
        except (TypeError, ValueError):
            return None
    earliest = parse_dt(stats.get("earliest_time"))
    latest = parse_dt(stats.get("latest_time"))
    if earliest and latest:
        return round((latest - earliest).total_seconds() / 3600, 2)
    return None


def pick_key_facts(context_item: dict[str, Any] | None, analysis_item: dict[str, Any] | None) -> list[str]:
    facts: list[str] = []
    if analysis_item:
        for item in analysis_item.get("key_facts", []):
            text = normalize_text(item)
            if text and text not in facts:
                facts.append(text)
    if context_item:
        timeline = context_item.get("timeline", [])
        if timeline:
            earliest = timeline[0]
            latest = timeline[-1]
            earliest_text = f"最早一条时间线信号来自 {normalize_text(earliest.get('source_name'))}：{normalize_text(earliest.get('title'))}"
            latest_text = f"最新一条时间线信号来自 {normalize_text(latest.get('source_name'))}：{normalize_text(latest.get('title'))}"
            for item in (earliest_text, latest_text):
                if item and item not in facts:
                    facts.append(item)
    return facts[:5]


def clean_basic_summary(summary: str, event_title: str) -> str:
    text = normalize_text(summary)
    if not text:
        return ""
    text = text.replace(f"该事件围绕“{event_title}”展开。", "")
    text = re.sub(r"过去\s*[0-9\.]+\s*小时内[^。]*。", "", text)
    text = re.sub(r"共出现\s*[0-9]+\s*条相关新闻[^。]*。", "", text)
    text = re.sub(r"主要标题包括：[^。]*。", "", text)
    return re.sub(r"\s+", " ", text).strip(" ，。")


def summarize_event(context_item: dict[str, Any] | None, analysis_item: dict[str, Any] | None) -> str:
    event_title = choose_event_title(context_item, analysis_item)
    summary = clean_basic_summary(normalize_text(analysis_item.get("summary") if analysis_item else ""), event_title)
    article_count = int((context_item or {}).get("total_articles") or 0)
    source_count = int((context_item or {}).get("unique_sources") or 0)
    time_span = infer_time_span_hours(context_item)
    representative_titles = (context_item or {}).get("representative_titles", [])[:2]
    title_text = "；".join(normalize_text(title) for title in representative_titles if normalize_text(title))
    parts = []
    if time_span is not None and article_count:
        parts.append(f"围绕“{event_title}”的相关报道在过去约 {time_span} 小时内明显升温，目前共聚合到 {article_count} 条信息，来自 {source_count} 个来源。")
    elif article_count:
        parts.append(f"围绕“{event_title}”的相关报道已聚合到 {article_count} 条，来自 {source_count} 个来源。")
    else:
        parts.append(f"当前热点主要围绕“{event_title}”展开。")
    if summary:
        parts.append(f"核心进展是：{summary}。")
    if title_text:
        parts.append(f"从代表性标题看，事件焦点集中在：{title_text}。")
    return " ".join(parts)


def top_retrieved_chunks(retrieved_item: dict[str, Any] | None, limit: int = 3) -> list[dict[str, Any]]:
    if not retrieved_item:
        return []
    return list(retrieved_item.get("retrieved_chunks", []))[:limit]


def trimmed_retrieved_chunks(retrieved_item: dict[str, Any] | None) -> list[dict[str, Any]]:
    chunks = top_retrieved_chunks(retrieved_item, limit=3)
    if not chunks:
        return []
    if len(chunks) == 1:
        return chunks
    top_score = int(chunks[0].get("score") or 0)
    second_score = int(chunks[1].get("score") or 0)
    if top_score >= second_score + 8:
        return [chunks[0]]
    return [chunk for chunk in chunks[:2] if int(chunk.get("score") or 0) >= max(1, top_score - 10)]


def normalize_watch_point_key(text: str) -> str:
    lowered = normalize_text(text).lower()
    replacements = {
        "是否真正": "",
        "是否进入": "进入",
        "是否有": "",
        "或商用": "商业化",
        "量产或商用阶段": "量产部署商业化阶段",
        "量产、部署或商业化阶段": "量产部署商业化阶段",
        "正式声明或谈判结果": "正式声明谈判结果",
        "谈判结果或制裁升级信号": "谈判结果制裁升级信号",
        "后续": "",
        "新的": "",
        "快速": "",
    }
    for old, new in replacements.items():
        lowered = lowered.replace(old, new)
    if "量产" in lowered or "部署" in lowered or "商业化" in lowered or "商用" in lowered:
        return "产品技术量产部署商业化"
    if "正式声明" in lowered and "谈判结果" in lowered:
        return "正式声明谈判结果"
    if "军事行动" in lowered or "制裁升级" in lowered:
        return "军事行动制裁升级"
    if "能源" in lowered and ("市场避险情绪" in lowered or "航运" in lowered or "地区安全指标" in lowered):
        return "能源航运避险情绪"
    if "财报" in lowered or "公告" in lowered or "监管信息" in lowered:
        return "财报公告监管"
    if "市场风险偏好" in lowered or "连续市场反应" in lowered or "持续性分化" in lowered:
        return "市场定价延续性"
    lowered = re.sub(r"[，。、“”‘’:,：\-\s]", "", lowered)
    return lowered


def watch_point_specificity_score(text: str) -> int:
    normalized = normalize_text(text)
    score = len(normalized)
    for keyword in ("真正", "部署", "商业化", "正式声明", "谈判结果", "制裁升级", "资本开支", "客户采用率", "监管信息"):
        if keyword in normalized:
            score += 10
    return score


def dedupe_watch_points(points: list[str]) -> list[str]:
    best_by_key: dict[str, str] = {}
    for point in points:
        text = normalize_text(point)
        if not text:
            continue
        key = normalize_watch_point_key(text)
        if not key:
            continue
        existing = best_by_key.get(key)
        if existing is None or watch_point_specificity_score(text) > watch_point_specificity_score(existing):
            best_by_key[key] = text

    unique: list[str] = []
    seen_keys: list[str] = []
    for key, text in sorted(best_by_key.items(), key=lambda item: (-watch_point_specificity_score(item[1]), item[0])):
        if any(key in seen or seen in key for seen in seen_keys):
            continue
        seen_keys.append(key)
        unique.append(text)
    return unique[:5]


def keyword_present(keyword: str, text: str) -> bool:
    lowered_text = normalize_text(text).lower()
    lowered_keyword = normalize_text(keyword).lower()
    if not lowered_text or not lowered_keyword:
        return False
    if re.search(r"[a-z]", lowered_keyword):
        if " " in lowered_keyword:
            pattern = rf"\b{re.escape(lowered_keyword)}\b"
        elif len(lowered_keyword) <= 3:
            pattern = rf"\b{re.escape(lowered_keyword)}\b"
        else:
            pattern = rf"\b{re.escape(lowered_keyword)}s?\b"
        return re.search(pattern, lowered_text, flags=re.IGNORECASE) is not None
    return lowered_keyword in lowered_text


def infer_effective_event_type(
    event_type: str,
    event_title: str,
    analysis_item: dict[str, Any] | None,
) -> str:
    combined = " ".join(
        [
            normalize_text(event_title).lower(),
            " ".join(normalize_text(item).lower() for item in (analysis_item or {}).get("key_facts", [])),
        ]
    )
    scores = {"geopolitics": 0, "markets": 0, "tech": 0}
    for candidate_type, keywords in EVENT_TYPE_OVERRIDE_KEYWORDS.items():
        for keyword in keywords:
            if keyword_present(keyword, combined):
                scores[candidate_type] += 1
    best_type, best_score = max(scores.items(), key=lambda item: item[1])
    current_type = event_type or "society"
    current_score = scores.get(current_type, 0)

    if current_type == "society":
        if best_score >= 2:
            return best_type
        return current_type

    if best_type != current_type and best_score >= 3 and best_score >= current_score + 2 and current_score == 0:
        return best_type

    return event_type or "society"


def build_expert_lens(
    event_type: str,
    retrieved_item: dict[str, Any] | None,
) -> str:
    chunks = trimmed_retrieved_chunks(retrieved_item)
    if not chunks:
        return NO_KNOWLEDGE_LENS_BY_TYPE.get(event_type, NO_KNOWLEDGE_LENS_BY_TYPE["society"])
    titles = [normalize_text(chunk.get("title")) for chunk in chunks if normalize_text(chunk.get("title"))]
    if event_type == "geopolitics":
        return f"从专家资料看，这类事件更应放在地区安全、能源通道和政策信号的框架下理解。当前更值得参考的专题包括：{'；'.join(titles[:2])}。"
    if event_type == "markets":
        return f"从专家资料看，这类事件更适合放在风险偏好、估值重定价和宏观约束的框架下分析。当前更相关的专题包括：{'；'.join(titles[:2])}。"
    if event_type == "tech":
        return f"从专家资料看，这类事件更应放在产业链、资本开支和生态竞争的框架下看。当前更相关的专题包括：{'；'.join(titles[:2])}。"
    return f"从已命中的知识卡片看，这类事件更适合放在通用风险框架中理解。当前更相关的专题包括：{'；'.join(titles[:2])}。"


def build_core_judgment(
    event_type: str,
    event_title: str,
    retrieved_item: dict[str, Any] | None,
) -> str:
    title_lower = event_title.lower()
    has_knowledge = bool(top_retrieved_chunks(retrieved_item))
    if event_type == "geopolitics":
        if any(token in title_lower for token in ("hormuz", "ceasefire", "truce", "sanctions", "iran")):
            return "这更像短期风险溢价与谈判博弈并存的地缘事件，而不是已经确认的长期格局逆转。"
        return "这更像政策与安全信号的重新定价事件，而不只是单一 headline 的情绪波动。"
    if event_type == "markets":
        return "这更像市场预期和风险偏好的再定价过程，而不是单一新闻本身决定全部方向。"
    if event_type == "tech":
        if any(token in title_lower for token in ("chip", "chips", "ai", "nvidia", "google", "openai")):
            return "这更像生态竞争和基础设施投入升级，而不只是一次单独产品发布。"
        return "这更像技术周期与商业化预期的变化，而不只是短期流量事件。"
    if has_knowledge:
        return "这更像一个具备延展性的结构性议题，而不是一次性热度事件。"
    return "这更像一条需要继续验证影响路径的热点事件，而不是已经完成定价的确定性主题。"


def build_why_it_matters(
    basic_item: dict[str, Any] | None,
    event_type: str,
    retrieved_item: dict[str, Any] | None,
) -> str:
    base = normalize_text(basic_item.get("why_it_matters") if basic_item else "")
    chunks = trimmed_retrieved_chunks(retrieved_item)
    if not chunks:
        return base or "该事件因多来源集中报道，具有进一步跟踪和验证的价值。"
    titles = "；".join(normalize_text(chunk.get("title")) for chunk in chunks if normalize_text(chunk.get("title")))
    if event_type == "geopolitics":
        return f"{base} 同时，已命中的专家卡片提示应重点关注安全信号、能源通道和地区升级路径，参考专题：{titles}。".strip()
    if event_type == "markets":
        return f"{base} 同时，专家资料提示要把它放进风险偏好、估值和宏观约束框架里理解，参考专题：{titles}。".strip()
    if event_type == "tech":
        return f"{base} 同时，专家资料提示应关注产业链、CAPEX 和生态竞争，而不只是产品 headline，参考专题：{titles}。".strip()
    return f"{base} 同时，命中的通用知识卡片提示需要继续观察影响路径是否真正落地，参考专题：{titles}。".strip()


def build_expert_context_used(retrieved_item: dict[str, Any] | None) -> list[dict[str, Any]]:
    chunks = trimmed_retrieved_chunks(retrieved_item)
    return [
        {
            "title": normalize_text(chunk.get("title")),
            "domain": normalize_text(chunk.get("domain")),
            "score": chunk.get("score"),
        }
        for chunk in chunks
    ]


def build_watch_points(
    basic_item: dict[str, Any] | None,
    event_type: str,
    retrieved_item: dict[str, Any] | None,
) -> list[str]:
    watch_points: list[str] = []
    event_specific = {
        "geopolitics": [
            "是否出现新的正式声明、谈判结果或制裁升级信号。",
            "能源、航运或地区安全指标是否快速变化。",
            "相关国家是否释放更明确的升级或克制动作。",
        ],
        "markets": [
            "市场风险偏好是否延续，还是只是一轮短期 headline 反应。",
            "后续财报、宏观数据或政策信号是否验证当前定价。",
            "相关板块和资产价格是否出现持续性分化。",
        ],
        "tech": [
            "产品或技术是否真正进入量产、部署或商业化阶段。",
            "竞争对手是否快速跟进并改变市场预期。",
            "资本开支、产业链或客户采用率是否继续强化。",
        ],
        "society": [
            "是否有更权威的信息源对事件细节进行确认。",
            "舆论热度是否转化为持续政策或公共议题影响。",
            "后续事实是否支持当前叙事方向。",
        ],
    }
    for item in event_specific.get(event_type, event_specific["society"]):
        if item not in watch_points:
            watch_points.append(item)

    if basic_item:
        for item in basic_item.get("watch_points", []):
            text = normalize_text(item)
            if text and text not in watch_points:
                watch_points.append(text)

    if trimmed_retrieved_chunks(retrieved_item):
        watch_points.append("已命中的知识卡片是否与新出现的新闻信号继续相互印证。")

    return dedupe_watch_points(watch_points)[:5]


def build_risk_assessment(
    event_type: str,
    heat_score: float,
    context_item: dict[str, Any] | None,
    retrieved_item: dict[str, Any] | None,
) -> str:
    source_count = int((context_item or {}).get("unique_sources") or 0)
    article_count = int((context_item or {}).get("total_articles") or 0)
    has_knowledge = bool(trimmed_retrieved_chunks(retrieved_item))
    if event_type == "geopolitics" and (heat_score >= 15 or source_count >= 4):
        return "high - 事件涉及地缘与安全信号，且多来源集中报道，短期风险定价和后续升级路径都值得高度关注。"
    if event_type in {"markets", "tech"} and (article_count >= 5 or has_knowledge):
        return "medium - 事件具备进一步发酵条件，但影响路径仍需由后续市场或产业信号确认。"
    return "low - 当前更像早期信号或局部主题，仍需等待更多确认信息。"


def build_market_impact(event_type: str, event_title: str, retrieved_item: dict[str, Any] | None) -> str:
    title_lower = event_title.lower()
    if event_type == "markets":
        return "可能直接影响市场风险偏好、估值容忍度和相关板块定价。"
    if event_type == "geopolitics":
        if any(token in title_lower for token in ("oil", "hormuz", "strait", "ceasefire", "iran")):
            return "可能影响油价、航运风险和整体市场避险情绪。"
        return "可能通过风险偏好变化间接影响市场，但直接定价强度仍需观察。"
    if event_type == "tech":
        return "可能影响相关科技公司估值、CAPEX 预期和产业链情绪。"
    if trimmed_retrieved_chunks(retrieved_item):
        return "存在一定间接市场影响，但更可能通过舆论和政策预期传导。"
    return "limited direct impact"


def build_geopolitical_impact(event_type: str, event_title: str) -> str:
    title_lower = event_title.lower()
    if event_type == "geopolitics":
        return "可能改变地区安全、谈判进程或外交博弈预期。"
    if any(token in title_lower for token in ("iran", "ukraine", "taiwan", "sanctions", "military", "ceasefire")):
        return "可能带有一定地缘外溢效应，需要关注跨国政策回应。"
    return "limited direct impact"


def build_uncertainty(
    context_item: dict[str, Any] | None,
    retrieved_item: dict[str, Any] | None,
) -> str:
    has_knowledge = bool(trimmed_retrieved_chunks(retrieved_item))
    article_count = int((context_item or {}).get("total_articles") or 0)
    if not has_knowledge:
        return "当前缺少足够的专题知识卡片支撑，部分判断仍主要依赖新闻聚合结果，后续需要更多背景材料验证。"
    if article_count < 4:
        return "当前样本量仍偏少，后续新增报道可能明显改变事件判断和影响路径。"
    return "当前主要不确定性在于后续信号是否会把 headline 事件推进成持续趋势，以及已有叙事是否会被新事实修正。"


def build_podcast_angle(event_type: str, event_title: str, retrieved_item: dict[str, Any] | None) -> str:
    if event_type == "geopolitics":
        return f"这件事真正要看的不是标题本身，而是 {event_title} 背后的谈判、威慑与能源通道风险如何被市场重新定价。"
    if event_type == "markets":
        return f"这条新闻更适合从“市场为什么现在重新定价”切入，而不是只复述 headline：{event_title}。"
    if event_type == "tech":
        return f"这件事背后真正值得讲的，是 {event_title} 所代表的产业链竞争和资本开支方向变化。"
    if trimmed_retrieved_chunks(retrieved_item):
        return f"这件事更适合从“标题背后的结构性矛盾是什么”切入，而不只是复述 {event_title}。"
    return f"这件事适合从“为什么这条新闻现在会形成热点”切入，先讲 headline，再讲影响路径。"


def build_podcast_candidate_score(
    basic_item: dict[str, Any] | None,
    context_item: dict[str, Any] | None,
    retrieved_item: dict[str, Any] | None,
    event_type: str,
) -> int:
    base_score = int((basic_item or {}).get("podcast_candidate_score") or 0)
    heat_score = float((basic_item or {}).get("heat_score") or (context_item or {}).get("heat_score") or 0)
    score = base_score
    if heat_score >= 15:
        score += 1
    if len(trimmed_retrieved_chunks(retrieved_item)) >= 2:
        score += 1
    if event_type in {"geopolitics", "markets", "tech"}:
        score += 1
    return max(0, min(10, score))


def build_expert_report_item(
    cluster_id: str,
    context_item: dict[str, Any] | None,
    analysis_item: dict[str, Any] | None,
    retrieved_item: dict[str, Any] | None,
) -> dict[str, Any]:
    rank = int(
        (analysis_item or {}).get("rank")
        or (context_item or {}).get("rank")
        or (retrieved_item or {}).get("rank")
        or 9999
    )
    event_title = choose_event_title(context_item, analysis_item)
    event_type = first_non_empty(
        (analysis_item or {}).get("event_type"),
        (retrieved_item or {}).get("event_type"),
        "society",
    )
    effective_event_type = infer_effective_event_type(event_type, event_title, analysis_item)
    heat_score = float(
        (analysis_item or {}).get("heat_score")
        or (context_item or {}).get("heat_score")
        or 0
    )

    return {
        "cluster_id": cluster_id,
        "rank": rank,
        "event_title": event_title,
        "event_type": event_type,
        "effective_event_type": effective_event_type,
        "heat_score": heat_score,
        "event_summary": summarize_event(context_item, analysis_item),
        "expert_lens": build_expert_lens(effective_event_type, retrieved_item),
        "core_judgment": build_core_judgment(effective_event_type, event_title, retrieved_item),
        "why_it_matters": build_why_it_matters(analysis_item, effective_event_type, retrieved_item),
        "key_facts": pick_key_facts(context_item, analysis_item),
        "expert_context_used": build_expert_context_used(retrieved_item),
        "risk_assessment": build_risk_assessment(effective_event_type, heat_score, context_item, retrieved_item),
        "market_impact": build_market_impact(effective_event_type, event_title, retrieved_item),
        "geopolitical_impact": build_geopolitical_impact(effective_event_type, event_title),
        "watch_points": build_watch_points(analysis_item, effective_event_type, retrieved_item),
        "uncertainty": build_uncertainty(context_item, retrieved_item),
        "podcast_angle": build_podcast_angle(effective_event_type, event_title, retrieved_item),
        "podcast_candidate_score": build_podcast_candidate_score(analysis_item, context_item, retrieved_item, effective_event_type),
    }


def build_output_path(base_dir: Path, source_type: str, generated_at: datetime) -> Path:
    output_dir = base_dir / EXPERT_REPORT_DIR
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir / f"{source_type}_expert_report_{generated_at.strftime('%Y-%m-%d_%H')}.json"


def build_expert_report_for_source_type(
    base_dir: Path,
    source_type: str,
    context_payload: dict[str, Any],
    context_file: Path,
    analysis_payload: dict[str, Any],
    analysis_file: Path,
    retrieved_payload: dict[str, Any],
    retrieved_file: Path,
) -> Path:
    context_map = index_by_cluster_id(context_payload.get("contexts", []))
    analysis_map = index_by_cluster_id(analysis_payload.get("analyses", []))
    retrieved_map = index_by_cluster_id(retrieved_payload.get("items", []))

    reports: list[dict[str, Any]] = []
    for cluster_id in build_ranked_cluster_ids(analysis_payload, context_payload, retrieved_payload):
        context_item = context_map.get(cluster_id)
        analysis_item = analysis_map.get(cluster_id)
        retrieved_item = retrieved_map.get(cluster_id)
        if not any((context_item, analysis_item, retrieved_item)):
            continue
        reports.append(build_expert_report_item(cluster_id, context_item, analysis_item, retrieved_item))

    reports.sort(key=lambda item: (int(item["rank"]), normalize_text(item["cluster_id"])))
    generated_at = now_dt()
    payload = {
        "generated_at": generated_at.strftime("%Y-%m-%d %H:%M:%S %z"),
        "source_type": source_type,
        "source_context_file": str(context_file),
        "source_analysis_file": str(analysis_file),
        "source_retrieved_file": str(retrieved_file),
        "report_count": len(reports),
        "reports": reports,
    }
    output_path = build_output_path(base_dir, source_type, generated_at)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate expert-enhanced reports from context, analysis, and retrieval data.")
    parser.add_argument("--source-type", choices=SUPPORTED_SOURCE_TYPES, help="Only run for the specified source type.")
    parser.add_argument("--context-file", help="Explicit cluster_context JSON file.")
    parser.add_argument("--analysis-file", help="Explicit basic_analysis JSON file.")
    parser.add_argument("--retrieved-file", help="Explicit retrieved_context JSON file.")
    return parser.parse_args()


def resolve_source_types(args: argparse.Namespace, base_dir: Path) -> list[str]:
    explicit_files = [args.context_file, args.analysis_file, args.retrieved_file]
    if args.source_type and any(explicit_files):
        inferred_types: set[str] = set()
        for file_path in explicit_files:
            if not file_path:
                continue
            payload, inferred = validate_explicit_file(Path(file_path).resolve(), args.source_type)
            inferred_types.add(inferred)
            _ = payload
        if len(inferred_types) > 1:
            raise ValueError("Explicit input files do not share the same source_type.")
        return [args.source_type]
    if args.source_type:
        return [args.source_type]
    if any(explicit_files):
        inferred_types: set[str] = set()
        for file_path in explicit_files:
            if not file_path:
                continue
            payload, inferred = validate_explicit_file(Path(file_path).resolve(), None)
            inferred_types.add(inferred)
            _ = payload
        if len(inferred_types) != 1:
            raise ValueError("Unable to infer a single source_type from explicit input files.")
        return list(inferred_types)
    return list(SUPPORTED_SOURCE_TYPES)


def main() -> None:
    args = parse_args()
    base_dir = project_root()
    source_types = resolve_source_types(args, base_dir)

    for source_type in source_types:
        context_payload, context_file, resolved_context_type = latest_or_explicit_payload(
            base_dir, source_type, args.context_file, "context"
        )
        analysis_payload, analysis_file, resolved_analysis_type = latest_or_explicit_payload(
            base_dir, source_type, args.analysis_file, "analysis"
        )
        retrieved_payload, retrieved_file, resolved_retrieved_type = latest_or_explicit_payload(
            base_dir, source_type, args.retrieved_file, "retrieved"
        )

        resolved_types = {resolved_context_type, resolved_analysis_type, resolved_retrieved_type}
        if len(resolved_types) != 1:
            raise ValueError("Input files do not share the same source_type.")

        output_path = build_expert_report_for_source_type(
            base_dir=base_dir,
            source_type=source_type,
            context_payload=context_payload,
            context_file=context_file,
            analysis_payload=analysis_payload,
            analysis_file=analysis_file,
            retrieved_payload=retrieved_payload,
            retrieved_file=retrieved_file,
        )
        print(f"[INFO] Expert report output: {output_path}")


if __name__ == "__main__":
    main()
