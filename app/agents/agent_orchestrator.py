# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import html
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from zoneinfo import ZoneInfo

from app.agents.agent_schemas import AgentPlan, AgentResponse, SUPPORTED_AGENT_SOURCE_TYPES, SUPPORTED_TASK_TYPES
from app.agents.memory_store import MemoryStore
from app.agents.reflection_checker import reflect_source_summary_results
from app.agents.task_planner import plan_user_request
from app.tools.article_reader import compact_article_reader_result, read_article, summarize_article_reader_result
from app.tools.article_reader.reader import build_text_excerpt, infer_content_quality
from app.tools.article_reader.source_policies import resolve_source_policy
from app.tools.news_search import search_news


TIMEZONE = "Asia/Shanghai"
AGENT_DATA_DIR = "data/agent"
AGENT_RESPONSE_DIR = "data/agent/responses"
SESSION_STATE_FILE = "data/agent/session_state.json"
RUNTIME_FALLBACK_DIR = ".agent_runtime"
SUPPORTED_SOURCE_TYPES = ("newsnow", "rss")
REPORT_SOURCES = (
    ("llm", "data/analysis/llm_reports", "{source_type}_llm_report_*.json", "items"),
    ("expert", "data/analysis/expert_reports", "{source_type}_expert_report_*.json", "reports"),
    ("basic", "data/analysis/reports", "{source_type}_basic_analysis_*.json", "analyses"),
    ("hot", "data/hot/{source_type}", "{source_type}_hot_clusters_*.json", "clusters"),
)
CONTEXT_DIR = "data/analysis/context"
CONTEXT_PATTERN = "{source_type}_cluster_context_*.json"
PIPELINE_STEPS = {
    "knowledge": {
        "script": "scripts/run_knowledge_ingest.py",
        "outputs": [("data/knowledge/processed", "chunks.jsonl")],
    },
    "hot": {
        "script": "scripts/run_hot_pipeline.py",
        "outputs": [
            ("data/hot/newsnow", "newsnow_hot_clusters_*.json"),
            ("data/hot/rss", "rss_hot_clusters_*.json"),
        ],
    },
    "context": {
        "script": "scripts/run_context_builder.py",
        "outputs": [
            ("data/analysis/context", "newsnow_cluster_context_*.json"),
            ("data/analysis/context", "rss_cluster_context_*.json"),
        ],
    },
    "basic": {
        "script": "scripts/run_basic_agent.py",
        "outputs": [
            ("data/analysis/reports", "newsnow_basic_analysis_*.json"),
            ("data/analysis/reports", "rss_basic_analysis_*.json"),
        ],
    },
    "retrieved": {
        "script": "scripts/run_retriever.py",
        "outputs": [
            ("data/analysis/retrieved_context", "newsnow_retrieved_context_*.json"),
            ("data/analysis/retrieved_context", "rss_retrieved_context_*.json"),
        ],
    },
    "expert": {
        "script": "scripts/run_expert_agent.py",
        "outputs": [
            ("data/analysis/expert_reports", "newsnow_expert_report_*.json"),
            ("data/analysis/expert_reports", "rss_expert_report_*.json"),
        ],
    },
    "llm": {
        "script": "scripts/run_llm_expert_writer.py",
        "outputs": [
            ("data/analysis/llm_reports", "newsnow_llm_report_*.json"),
            ("data/analysis/llm_reports", "rss_llm_report_*.json"),
        ],
    },
}

ZH_ENTITY_SYNONYMS = {
    "伊朗": ("iran", "伊朗"),
    "以色列": ("israel", "以色列"),
    "特朗普": ("trump", "特朗普"),
    "美国": ("u.s.", "us", "america", "美国"),
    "中国": ("china", "中国"),
    "访华": ("china", "visit", "访华"),
    "乌克兰": ("ukraine", "乌克兰"),
    "俄罗斯": ("russia", "俄罗斯"),
    "欧盟": ("eu", "europe", "欧盟"),
    "霍尔木兹": ("hormuz", "strait", "霍尔木兹"),
    "台海": ("taiwan", "strait", "台海"),
    "台湾": ("taiwan", "台湾"),
    "芯片": ("chip", "chips", "semiconductor", "芯片"),
    "人工智能": ("ai", "artificial intelligence", "人工智能"),
    "英伟达": ("nvidia", "英伟达"),
    "谷歌": ("google", "谷歌"),
    "苹果": ("apple", "苹果"),
    "特斯拉": ("tesla", "特斯拉"),
    "油价": ("oil", "crude", "油价"),
    "战争": ("war", "conflict", "战争"),
    "停火": ("ceasefire", "truce", "停火"),
    "制裁": ("sanctions", "制裁"),
    "赤字": ("deficit", "赤字"),
    "财政": ("fiscal", "财政"),
    "股市": ("stocks", "market", "股市"),
}
QUERY_STOPWORDS = {
    "过去",
    "最近",
    "新闻",
    "热点",
    "专家",
    "角度",
    "分析",
    "预测",
    "影响",
    "一下",
    "关于",
    "事件",
    "做出",
    "总结",
    "整理",
    "内容",
    "观点",
    "the",
    "and",
    "for",
    "with",
    "about",
    "news",
    "analysis",
}


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def now_dt() -> datetime:
    return datetime.now(ZoneInfo(TIMEZONE))


def now_text() -> str:
    return now_dt().strftime("%Y-%m-%d %H:%M:%S %z")


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


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


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def try_write_json(path: Path, payload: dict[str, Any], fallback_path: Path | None = None) -> Path | None:
    try:
        write_json(path, payload)
    except OSError as exc:
        if fallback_path is None:
            print(f"[WARN] Unable to write JSON file {path}: {exc}")
            return None
        try:
            write_json(fallback_path, payload)
        except OSError as fallback_exc:
            print(f"[WARN] Unable to write JSON file {path}: {exc}")
            print(f"[WARN] Unable to write fallback JSON file {fallback_path}: {fallback_exc}")
            return None
        print(f"[WARN] Unable to write JSON file {path}: {exc}")
        print(f"[INFO] Wrote fallback JSON file: {fallback_path}")
        return fallback_path
    return path


def strip_html(value: Any) -> str:
    text = html.unescape(normalize_text(value))
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def truncate_text(text: str, limit: int = 220) -> str:
    clean = normalize_text(text)
    if len(clean) <= limit:
        return clean
    return clean[: limit - 1].rstrip() + "…"


def format_beijing_time(value: Any) -> str:
    parsed = parse_dt(value)
    if parsed is None:
        return normalize_text(value)
    return parsed.strftime("%Y-%m-%d %H:%M:%S %z")


def article_published_time(article: dict[str, Any]) -> str:
    published_at = format_beijing_time(article.get("published_at"))
    if published_at:
        return published_at
    return "未知"


def find_latest_file(base_dir: Path, directory: str, pattern: str) -> Path | None:
    candidates = sorted((base_dir / directory).glob(pattern))
    if not candidates:
        return None
    return candidates[-1]


def load_latest_context_map(base_dir: Path, source_type: str) -> tuple[dict[str, dict[str, Any]], Path | None]:
    path = find_latest_file(base_dir, CONTEXT_DIR, CONTEXT_PATTERN.format(source_type=source_type))
    if path is None:
        return {}, None
    payload = load_json(path)
    context_map = {
        normalize_text(item.get("cluster_id")): item
        for item in payload.get("contexts", [])
        if normalize_text(item.get("cluster_id"))
    }
    return context_map, path


def load_latest_retrieved_context_map(base_dir: Path, source_type: str) -> dict[str, dict[str, Any]]:
    path = find_latest_file(
        base_dir,
        "data/analysis/retrieved_context",
        f"{source_type}_retrieved_context_*.json",
    )
    if path is None:
        return {}
    payload = load_json(path)
    return {
        normalize_text(item.get("cluster_id")): item
        for item in payload.get("items", [])
        if normalize_text(item.get("cluster_id"))
    }


def coerce_expert_context_from_retrieved(item: dict[str, Any]) -> list[dict[str, Any]]:
    contexts: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    for chunk in item.get("retrieved_chunks", []):
        context_title = normalize_text(chunk.get("title"))
        context_source_path = normalize_text(chunk.get("source_path"))
        dedup_key = f"{context_source_path}::{context_title}"
        if dedup_key in seen_keys:
            continue
        seen_keys.add(dedup_key)
        contexts.append(
            {
                "title": context_title,
                "domain": normalize_text(chunk.get("domain")),
                "score": chunk.get("score"),
                "source_path": context_source_path,
                "chunk_id": normalize_text(chunk.get("chunk_id")),
            }
        )
        if len(contexts) >= 3:
            break
    return contexts


def coerce_event_title(item: dict[str, Any]) -> str:
    title = normalize_text(item.get("event_title"))
    if title:
        return title
    titles = item.get("representative_titles") or []
    if titles:
        return normalize_text(titles[0])
    return normalize_text(item.get("title"))


def coerce_summary(item: dict[str, Any]) -> str:
    for key in ("final_summary", "event_summary", "summary", "why_it_really_matters", "expert_analysis"):
        value = strip_html(item.get(key))
        if value:
            return truncate_text(value, 180)
    titles = item.get("representative_titles") or []
    if titles:
        return truncate_text("；".join(normalize_text(title) for title in titles[:2] if normalize_text(title)), 180)
    return ""


def load_ranked_items_for_source(base_dir: Path, source_type: str) -> list[dict[str, Any]]:
    context_map, context_file = load_latest_context_map(base_dir, source_type)
    for report_kind, directory_template, pattern_template, collection_key in REPORT_SOURCES:
        directory = directory_template.format(source_type=source_type)
        pattern = pattern_template.format(source_type=source_type)
        path = find_latest_file(base_dir, directory, pattern)
        if path is None:
            continue

        payload = load_json(path)
        items = payload.get(collection_key, [])
        ranked_items: list[dict[str, Any]] = []
        for item in items:
            cluster_id = normalize_text(item.get("cluster_id"))
            context_item = context_map.get(cluster_id, {})
            articles = context_item.get("articles") or item.get("articles") or []
            sources = context_item.get("sources") or item.get("sources") or []
            ranked_items.append(
                {
                    "source_type": source_type,
                    "cluster_id": cluster_id,
                    "rank": int(item.get("rank") or 9999),
                    "event_title": coerce_event_title(item),
                    "summary": coerce_summary(item),
                    "heat_score": float(item.get("heat_score") or 0),
                    "sources": sources,
                    "article_ids": context_item.get("article_ids") or item.get("article_ids") or [],
                    "articles": articles,
                    "report_kind": report_kind,
                    "source_file": str(path),
                    "context_file": str(context_file) if context_file else None,
                    "generated_at": normalize_text(payload.get("generated_at")),
                }
            )
        ranked_items.sort(key=lambda value: (int(value["rank"]), -float(value["heat_score"])))
        return ranked_items
    return []


def resolve_source_types(source_type: str) -> list[str]:
    if source_type == "mixed":
        return list(SUPPORTED_SOURCE_TYPES)
    if source_type in SUPPORTED_SOURCE_TYPES:
        return [source_type]
    return list(SUPPORTED_SOURCE_TYPES)


def save_session_state(base_dir: Path, payload: dict[str, Any]) -> Path | None:
    return MemoryStore(base_dir).save(payload)


def load_session_state(base_dir: Path) -> dict[str, Any]:
    return MemoryStore(base_dir).load()


def record_agent_interaction(base_dir: Path, plan: AgentPlan, response: AgentResponse) -> Path | None:
    return MemoryStore(base_dir).append_interaction(
        task_type=plan.task_type,
        query=plan.user_query,
        answer=response.answer_text,
        output_file=response.output_file,
    )


def build_response_path(base_dir: Path) -> Path:
    timestamp = now_dt().strftime("%Y-%m-%d_%H%M%S")
    return base_dir / AGENT_RESPONSE_DIR / f"agent_response_{timestamp}.json"


def build_fallback_response_path(base_dir: Path) -> Path:
    timestamp = now_dt().strftime("%Y-%m-%d_%H%M%S")
    return base_dir / RUNTIME_FALLBACK_DIR / "responses" / f"agent_response_{timestamp}.json"


def outputs_exist(base_dir: Path, step_name: str) -> bool:
    step = PIPELINE_STEPS[step_name]
    for directory, pattern in step["outputs"]:
        if not list((base_dir / directory).glob(pattern)):
            return False
    return True


def has_knowledge_sources(base_dir: Path) -> bool:
    return any((base_dir / "data/knowledge/sources").glob("**/*.txt"))


def run_pipeline_script(base_dir: Path, step_name: str) -> None:
    script = normalize_text(PIPELINE_STEPS[step_name]["script"])
    print(f"[PIPELINE] Running {script}")
    completed = subprocess.run(
        [sys.executable, script],
        cwd=base_dir,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if completed.returncode != 0:
        raise RuntimeError(f"Pipeline step failed: {script} (exit code {completed.returncode})")


def required_pipeline_steps(plan: AgentPlan, skip_llm: bool) -> list[str]:
    if plan.task_type == "source_summary_request":
        return []

    steps = ["hot", "context", "basic"]

    if plan.task_type == "expert_topic_analysis":
        steps = ["hot", "context", "basic", "knowledge", "retrieved", "expert"]
        if not skip_llm:
            steps.append("llm")
        return steps

    if plan.task_type == "hot_news_query":
        steps.extend(["knowledge", "retrieved", "expert"])
        if not skip_llm:
            steps.append("llm")
        return steps

    return steps


def ensure_pipeline_outputs(
    base_dir: Path,
    plan: AgentPlan,
    force_pipeline: bool = False,
    skip_llm: bool = False,
) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []
    for step_name in required_pipeline_steps(plan, skip_llm):
        if step_name == "knowledge" and not has_knowledge_sources(base_dir):
            results.append({"step": step_name, "status": "skipped", "reason": "no knowledge txt sources"})
            continue

        if not force_pipeline and outputs_exist(base_dir, step_name):
            results.append({"step": step_name, "status": "reused"})
            continue

        run_pipeline_script(base_dir, step_name)
        results.append({"step": step_name, "status": "generated"})
    return results


def run_hot_news_query(base_dir: Path, plan: AgentPlan) -> AgentResponse:
    params = plan.params
    source_types = resolve_source_types(normalize_text(params.get("source_type")) or "mixed")
    limit = int(params.get("limit") or 10)
    window_hours = int(params.get("window_hours") or 24)

    merged: list[dict[str, Any]] = []
    for source_type in source_types:
        merged.extend(load_ranked_items_for_source(base_dir, source_type))

    merged.sort(key=lambda item: (-float(item.get("heat_score") or 0), item["source_type"], int(item.get("rank") or 9999)))
    selected = merged[:limit]

    if not selected:
        answer = "没有找到可用的热点结果。请先运行热点发现链路，例如 `python scripts/run_hot_pipeline.py`。"
        return AgentResponse(plan.task_type, answer, {"items": [], "plan": plan.to_dict()})

    display_items: list[dict[str, Any]] = []
    lines = [f"过去约 {window_hours} 小时的热点新闻如下（v1 基于本地最新热点分析结果）："]
    for index, item in enumerate(selected, start=1):
        display_item = dict(item)
        display_item["display_index"] = index
        display_items.append(display_item)
        source_label = item["source_type"].upper()
        summary = item.get("summary") or "暂无摘要"
        lines.append(f"{index}. [{source_label}] {item['event_title']}：{summary}")
    lines.append("请问你对哪些感兴趣？你可以继续说：`请对1，2做内容整理和总结`。")

    session_payload = {
        "updated_at": now_text(),
        "last_task_type": plan.task_type,
        "last_query": plan.user_query,
        "last_hot_news": display_items,
    }
    session_path = save_session_state(base_dir, session_payload)

    data = {
        "plan": plan.to_dict(),
        "items": display_items,
        "session_state_file": str(session_path) if session_path else None,
        "note": "v1 uses the latest generated hot/expert files. Run the pipeline before asking for fresh results.",
    }
    return AgentResponse(plan.task_type, "\n".join(lines), data)


def pick_representative_articles(articles: list[dict[str, Any]], max_sources: int = 8) -> list[dict[str, Any]]:
    by_source: dict[str, dict[str, Any]] = {}
    for article in articles:
        source = normalize_text(article.get("source_name")) or "Unknown source"
        existing = by_source.get(source)
        if existing is None:
            by_source[source] = article
            continue
        existing_time = parse_dt(existing.get("resolved_time") or existing.get("published_at") or existing.get("fetched_at"))
        article_time = parse_dt(article.get("resolved_time") or article.get("published_at") or article.get("fetched_at"))
        if article_time and (existing_time is None or article_time > existing_time):
            by_source[source] = article
    return list(by_source.values())[:max_sources]


def objective_article_summary(
    article: dict[str, Any],
    reader_result: dict[str, Any] | None = None,
) -> tuple[str, str]:
    if reader_result:
        reader_summary = summarize_article_reader_result(reader_result)
        if reader_summary:
            return reader_summary, "article_reader"

    summary = strip_html(article.get("summary"))
    if summary:
        return truncate_text(summary, 320), "database_summary"
    title = normalize_text(article.get("title"))
    if title:
        return f"数据库中暂无正文摘要；当前可确认的新闻标题是：{title}", "database_title"
    return "数据库中暂无可用摘要。", "fallback"


def classify_content_fetch_status(reader_result: dict[str, Any], summary_source: str) -> str:
    reader_status = normalize_text(reader_result.get("content_fetch_status") or reader_result.get("status"))
    if summary_source == "article_reader" and reader_status == "success":
        return "full_text"
    if summary_source in {"database_summary", "database_title"}:
        if reader_status == "summary_only":
            return "summary_only"
        if reader_status in {"failed", "skipped", ""}:
            return "summary_fallback"
    return reader_status or "unknown"


def content_fetch_reason(reader_result: dict[str, Any], summary_source: str) -> str:
    if summary_source == "article_reader":
        return normalize_text(reader_result.get("extraction_source")) or "article_reader"
    error = normalize_text(reader_result.get("error"))
    if error:
        return error
    return summary_source


def rss_content_reader_result(article: dict[str, Any], policy_reason: str) -> dict[str, Any]:
    text = strip_html(article.get("content"))
    if not text:
        return {
            "status": "failed",
            "content_fetch_status": "summary_only",
            "content_quality": "none",
            "http_status": None,
            "error": policy_reason or "rss_content policy set but stored RSS content is empty",
            "from_cache": False,
        }
    return {
        "status": "success",
        "content_fetch_status": "success",
        "content_quality": infer_content_quality(text),
        "http_status": None,
        "error": "",
        "title": normalize_text(article.get("title")),
        "text": text,
        "text_excerpt": build_text_excerpt(text),
        "char_count": len(text),
        "extraction_source": "rss_content_encoded",
        "from_cache": False,
    }


def policy_disabled_reader_result(policy_reason: str) -> dict[str, Any]:
    return {
        "status": "skipped",
        "content_fetch_status": "summary_only",
        "content_quality": "none",
        "http_status": None,
        "error": policy_reason or "article reading disabled for this RSS source",
        "from_cache": False,
    }


def read_article_with_source_policy(article: dict[str, Any], base_dir: Path) -> dict[str, Any]:
    url = normalize_text(article.get("url"))
    if normalize_text(article.get("source_type")) != "rss":
        return read_article(url, base_dir) if url else {"status": "skipped", "error": "missing url"}

    policy = resolve_source_policy(
        base_dir,
        source_id=normalize_text(article.get("source_id")),
    )
    article_reading = normalize_text(policy.get("article_reading"))
    policy_reason = normalize_text(policy.get("reason"))
    if article_reading == "rss_content":
        return rss_content_reader_result(article, policy_reason)
    if article_reading == "disabled":
        return policy_disabled_reader_result(policy_reason)
    return read_article(url, base_dir) if url else {"status": "skipped", "error": "missing url"}


def run_source_summary_request(base_dir: Path, plan: AgentPlan) -> AgentResponse:
    session = load_session_state(base_dir)
    hot_items = session.get("last_hot_news", [])
    selected_indexes = plan.params.get("selected_indexes") or []

    if not hot_items:
        answer = "还没有可引用的热点列表。请先问我：`告诉我过去24小时的10条热点新闻`。"
        return AgentResponse(plan.task_type, answer, {"plan": plan.to_dict(), "items": []})
    if not selected_indexes:
        answer = "我没有识别到要整理的编号。请用类似 `请对1，2做内容整理和总结` 的格式告诉我。"
        return AgentResponse(plan.task_type, answer, {"plan": plan.to_dict(), "items": []})

    item_by_index = {int(item["display_index"]): item for item in hot_items if item.get("display_index")}
    results: list[dict[str, Any]] = []
    lines = ["好的。下面是你选择的热点对应的来源整理。v1 会优先读取原文并做客观摘要，读取失败时回退到本地数据库摘要，不额外加入专家判断。"]

    for selected_index in selected_indexes:
        hot_item = item_by_index.get(int(selected_index))
        if not hot_item:
            lines.append(f"\n新闻{selected_index}：没有在上一轮热点列表中找到这个编号。")
            continue

        articles = pick_representative_articles(hot_item.get("articles", []), max_sources=5)
        lines.append(f"\n新闻{selected_index}：{hot_item.get('event_title')}")
        if not articles:
            lines.append("暂时没有找到可展开的文章列表。")
            results.append({"display_index": selected_index, "event_title": hot_item.get("event_title"), "sources": []})
            continue

        source_summaries: list[dict[str, Any]] = []
        for article in articles:
            source_name = normalize_text(article.get("source_name")) or "Unknown source"
            url = normalize_text(article.get("url"))
            reader_result = read_article_with_source_policy(article, base_dir)
            summary, summary_source = objective_article_summary(article, reader_result)
            published_time = article_published_time(article)
            compact_reader = compact_article_reader_result(reader_result)
            reader_status = compact_reader.get("status") or "unknown"
            content_status = classify_content_fetch_status(compact_reader, summary_source)
            content_quality = normalize_text(compact_reader.get("content_quality")) or "unknown"
            extraction_source = normalize_text(compact_reader.get("extraction_source"))
            reader_note = "成功"
            if reader_status != "success":
                reader_note = f"失败：{compact_reader.get('error') or 'unknown error'}"
            elif compact_reader.get("from_cache"):
                reader_note = f"成功（缓存，{extraction_source or 'reader'}）"
            elif extraction_source == "jina_reader":
                reader_note = "成功（Jina Reader）"
            lines.append(f"- {source_name}：{url or '无 URL'}")
            lines.append(f"  Published time: {published_time}")
            lines.append(f"  原文读取：{reader_note}")
            lines.append(f"  内容状态：{content_status}（quality={content_quality}）")
            lines.append(f"  主要内容：{summary}")
            source_summaries.append(
                {
                    "source_name": source_name,
                    "title": normalize_text(article.get("title")),
                    "url": url,
                    "published_time_beijing": published_time,
                    "published_at": normalize_text(article.get("published_at")),
                    "fetched_at": normalize_text(article.get("fetched_at")),
                    "summary": summary,
                    "summary_source": summary_source,
                    "content_fetch_status": content_status,
                    "content_fetch_reason": content_fetch_reason(compact_reader, summary_source),
                    "content_quality": content_quality,
                    "article_reader": compact_reader,
                }
            )

        results.append(
            {
                "display_index": selected_index,
                "source_type": hot_item.get("source_type"),
                "cluster_id": hot_item.get("cluster_id"),
                "event_title": hot_item.get("event_title"),
                "sources": source_summaries,
            }
        )

    reflection_notes = reflect_source_summary_results(results)
    if reflection_notes:
        lines.append("\n自检提示：")
        for note in reflection_notes:
            lines.append(f"- {note}")

    return AgentResponse(
        plan.task_type,
        "\n".join(lines),
        {"plan": plan.to_dict(), "items": results, "reflection_notes": reflection_notes},
    )


def extract_query_keywords(query: str) -> list[str]:
    lowered = query.lower()
    keywords: list[str] = []

    for zh_term, synonyms in ZH_ENTITY_SYNONYMS.items():
        if zh_term in query:
            keywords.extend(synonyms)

    for token in re.findall(r"[a-zA-Z][a-zA-Z0-9\.\-]{1,}", lowered):
        if token not in QUERY_STOPWORDS and len(token) >= 2:
            keywords.append(token)

    for token in re.findall(r"[\u4e00-\u9fff]{2,}", query):
        if token not in QUERY_STOPWORDS and len(token) >= 2:
            keywords.append(token)

    seen: set[str] = set()
    result: list[str] = []
    for keyword in keywords:
        normalized = keyword.lower().strip()
        if not normalized or normalized in seen or normalized in QUERY_STOPWORDS:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


def infer_topic_domain(query: str, keywords: list[str]) -> str:
    text = " ".join([query.lower(), *keywords])
    if any(token in text for token in ("iran", "israel", "ukraine", "russia", "taiwan", "hormuz", "war", "ceasefire", "sanctions", "trump", "外交", "战争", "停火", "制裁")):
        return "geopolitics"
    if any(token in text for token in ("market", "stocks", "oil", "deficit", "fiscal", "earnings", "revenue", "股市", "油价", "财政", "赤字")):
        return "markets"
    if any(token in text for token in ("ai", "chip", "nvidia", "google", "openai", "tesla", "semiconductor", "芯片", "人工智能", "英伟达")):
        return "tech"
    return "unknown"


def load_latest_report_items(base_dir: Path, source_type: str) -> list[dict[str, Any]]:
    for report_kind, directory_template, pattern_template, collection_key in REPORT_SOURCES[:2]:
        directory = directory_template.format(source_type=source_type)
        pattern = pattern_template.format(source_type=source_type)
        path = find_latest_file(base_dir, directory, pattern)
        if path is None:
            continue
        payload = load_json(path)
        retrieved_context_map = load_latest_retrieved_context_map(base_dir, source_type)
        expert_path: Path | None = None
        expert_context_map: dict[str, dict[str, Any]] = {}
        if report_kind == "llm":
            expert_path = find_latest_file(
                base_dir,
                "data/analysis/expert_reports",
                f"{source_type}_expert_report_*.json",
            )
            if expert_path is not None:
                expert_payload = load_json(expert_path)
                expert_context_map = {
                    normalize_text(report.get("cluster_id")): report
                    for report in expert_payload.get("reports", [])
                    if normalize_text(report.get("cluster_id"))
                }

        items: list[dict[str, Any]] = []
        for item in payload.get(collection_key, []):
            copied = dict(item)
            cluster_id = normalize_text(copied.get("cluster_id"))
            retrieved_item = retrieved_context_map.get(cluster_id)
            expert_context = expert_context_map.get(cluster_id)
            if retrieved_item:
                copied["expert_context_used"] = coerce_expert_context_from_retrieved(retrieved_item)
            elif expert_context:
                copied["expert_context_used"] = expert_context.get("expert_context_used", [])
                copied["source_expert_report_file"] = str(expert_path)
            copied["source_type"] = source_type
            copied["report_kind"] = report_kind
            copied["source_file"] = str(path)
            items.append(copied)
        return items
    return []


def item_search_text(item: dict[str, Any]) -> str:
    pieces = [
        item.get("event_title"),
        item.get("final_summary"),
        item.get("expert_analysis"),
        item.get("why_it_really_matters"),
        item.get("event_summary"),
        item.get("why_it_matters"),
        item.get("key_risk"),
        item.get("uncertainty"),
        item.get("podcast_hook"),
    ]
    pieces.extend(item.get("key_facts", []) or [])
    pieces.extend(item.get("watch_points", []) or [])
    return "\n".join(normalize_text(piece) for piece in pieces if normalize_text(piece)).lower()


def keyword_matches_text(keyword: str, text: str) -> bool:
    normalized_keyword = normalize_text(keyword).lower()
    normalized_text = normalize_text(text).lower()
    if not normalized_keyword or not normalized_text:
        return False
    if re.search(r"[a-z]", normalized_keyword):
        pattern = rf"(?<![a-z0-9]){re.escape(normalized_keyword)}(?![a-z0-9])"
        return re.search(pattern, normalized_text, flags=re.IGNORECASE) is not None
    return normalized_keyword in normalized_text


def score_report_item(item: dict[str, Any], keywords: list[str]) -> int:
    haystack = item_search_text(item)
    title = normalize_text(item.get("event_title")).lower()
    score = 0
    title_hits = 0
    for keyword in keywords:
        if keyword_matches_text(keyword, title):
            score += 5
            title_hits += 1
        elif keyword_matches_text(keyword, haystack):
            score += 1
    if title_hits == 0 and score < 8:
        return 0
    return score


def build_expert_answer_from_reports(topic: str, domain: str, matches: list[tuple[int, dict[str, Any]]]) -> str:
    lines = [f"我先把这个问题归类为：{domain}。下面是当前已生成专家报告中最相关的结果。"]
    for index, (score, item) in enumerate(matches[:3], start=1):
        title = coerce_event_title(item)
        summary = normalize_text(item.get("final_summary") or item.get("event_summary") or item.get("event_summary"))
        analysis = normalize_text(item.get("expert_analysis") or item.get("core_judgment") or item.get("why_it_really_matters"))
        risk = normalize_text(item.get("key_risk") or item.get("risk_assessment"))
        expert_context_used = item.get("expert_context_used", []) or []
        lines.append(f"\n{index}. {title}")
        if summary:
            lines.append(f"   摘要：{truncate_text(summary, 260)}")
        if analysis:
            lines.append(f"   专家判断：{truncate_text(analysis, 360)}")
        if risk:
            lines.append(f"   关键风险：{truncate_text(risk, 220)}")
        if expert_context_used:
            lines.append("   调用的专家知识：")
            for context in expert_context_used[:3]:
                context_title = normalize_text(context.get("title")) or "unknown"
                context_domain = normalize_text(context.get("domain")) or "unknown"
                context_score = context.get("score")
                context_source_path = normalize_text(context.get("source_path"))
                if context_source_path:
                    lines.append(
                        f"   - {context_title}（domain={context_domain}, score={context_score}, source={context_source_path}）"
                    )
                else:
                    lines.append(f"   - {context_title}（domain={context_domain}, score={context_score}）")
        else:
            lines.append("   调用的专家知识：未命中明确知识卡片，主要复用已有专家报告字段。")
        lines.append(f"   匹配分：{score}，来源：{item.get('source_type')} / {item.get('report_kind')}")
    lines.append("\n说明：v1 先复用已有 expert_report / llm_report，不会临时重新抓网页或重新跑完整 RAG。")
    return "\n".join(lines)


def build_objective_news_fallback(topic: str, domain: str, rows: list[dict[str, Any]]) -> str:
    if not rows:
        return (
            f"我把这个问题初步归类为：{domain}，但当前热点报告和 SQLite 中都没有找到足够相关的数据。\n"
            "如果这是一个新问题，请先运行采集和热点分析链路；如果这是专家知识库尚未覆盖的领域，我会先避免做主观专家判断。"
        )
    lines = [
        f"我把这个问题初步归类为：{domain}，但当前没有命中足够强的专家报告。",
        "下面只做新闻源层面的客观整理，不加入专家主观判断：",
    ]
    for index, row in enumerate(rows, start=1):
        summary, _summary_source = objective_article_summary(row)
        lines.append(f"{index}. [{row.get('source_type')}] {row.get('source_name')}：{row.get('title')}")
        lines.append(f"   URL：{row.get('url')}")
        lines.append(f"   主要内容：{summary}")
    return "\n".join(lines)


def run_expert_topic_analysis(base_dir: Path, plan: AgentPlan) -> AgentResponse:
    params = plan.params
    topic = normalize_text(params.get("topic")) or plan.user_query
    window_hours = int(params.get("window_hours") or 24)
    source_types = resolve_source_types(normalize_text(params.get("source_type")) or "mixed")
    keywords = extract_query_keywords(plan.user_query + " " + topic)
    domain = infer_topic_domain(plan.user_query, keywords)

    scored: list[tuple[int, dict[str, Any]]] = []
    for source_type in source_types:
        for item in load_latest_report_items(base_dir, source_type):
            score = score_report_item(item, keywords)
            if score > 0:
                scored.append((score, item))

    scored.sort(key=lambda pair: (-pair[0], int(pair[1].get("rank") or 9999)))
    if scored:
        answer = build_expert_answer_from_reports(topic, domain, scored)
        return AgentResponse(
            plan.task_type,
            answer,
            {
                "plan": plan.to_dict(),
                "topic": topic,
                "domain": domain,
                "keywords": keywords,
                "mode": "expert_report_reuse",
                "matches": [item for _, item in scored[:5]],
            },
        )

    rows = search_news(
        base_dir=base_dir,
        query=f"{plan.user_query} {topic}",
        window_hours=window_hours,
        source_type=normalize_text(params.get("source_type")) or "mixed",
        limit=12,
    )
    answer = build_objective_news_fallback(topic, domain, rows)
    return AgentResponse(
        plan.task_type,
        answer,
        {
            "plan": plan.to_dict(),
            "topic": topic,
            "domain": domain,
            "keywords": keywords,
            "mode": "objective_source_summary_only",
            "articles": rows,
        },
    )


def execute_plan(base_dir: Path, plan: AgentPlan) -> AgentResponse:
    if plan.task_type == "hot_news_query":
        return run_hot_news_query(base_dir, plan)
    if plan.task_type == "source_summary_request":
        return run_source_summary_request(base_dir, plan)
    if plan.task_type == "expert_topic_analysis":
        return run_expert_topic_analysis(base_dir, plan)
    raise ValueError(f"Unsupported task_type: {plan.task_type}")


def run_agent_once(
    query: str,
    task_type: str | None = None,
    source_type: str | None = None,
    limit: int | None = None,
    window_hours: int | None = None,
    auto_pipeline: bool = True,
    force_pipeline: bool = False,
    skip_llm: bool = False,
    base_dir: Path | None = None,
) -> AgentResponse:
    base = base_dir or project_root()
    overrides = {
        "source_type": source_type,
        "limit": limit,
        "window_hours": window_hours,
    }
    plan = plan_user_request(query, task_type, overrides)
    pipeline_steps: list[dict[str, str]] = []
    if auto_pipeline:
        pipeline_steps = ensure_pipeline_outputs(base, plan, force_pipeline=force_pipeline, skip_llm=skip_llm)
    response = execute_plan(base, plan)
    response.data["pipeline_steps"] = pipeline_steps
    output_payload = {
        "generated_at": now_text(),
        "plan": plan.to_dict(),
        "response": response.to_dict(),
    }
    output_path = build_response_path(base)
    actual_output_path = try_write_json(output_path, output_payload, build_fallback_response_path(base))
    if actual_output_path:
        response.output_file = str(actual_output_path)
    record_agent_interaction(base, plan, response)
    return response


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the lightweight news analysis agent v1.")
    parser.add_argument("--query", "-q", help="User request to process.")
    parser.add_argument("--task-type", choices=SUPPORTED_TASK_TYPES, help="Optional explicit task type.")
    parser.add_argument("--source-type", choices=SUPPORTED_AGENT_SOURCE_TYPES, help="Optional source type.")
    parser.add_argument("--limit", type=int, help="Optional result limit for hot news.")
    parser.add_argument("--window-hours", type=int, help="Optional time window in hours.")
    parser.add_argument("--no-auto-pipeline", action="store_true", help="Do not run missing prerequisite pipeline steps.")
    parser.add_argument("--force-pipeline", action="store_true", help="Regenerate prerequisite pipeline outputs before answering.")
    parser.add_argument("--skip-llm", action="store_true", help="Do not run the LLM expert writer step during auto pipeline preparation.")
    parser.add_argument("--json", action="store_true", help="Print the full JSON response instead of only answer text.")
    return parser.parse_args()


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    args = parse_args()
    query = normalize_text(args.query)
    if not query:
        query = input("请输入你的新闻分析请求：").strip()
    response = run_agent_once(
        query=query,
        task_type=args.task_type,
        source_type=args.source_type,
        limit=args.limit,
        window_hours=args.window_hours,
        auto_pipeline=not args.no_auto_pipeline,
        force_pipeline=args.force_pipeline,
        skip_llm=args.skip_llm,
    )
    if args.json:
        print(json.dumps(response.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(response.answer_text)
        if response.output_file:
            print(f"\n[INFO] Agent response saved to: {response.output_file}")


if __name__ == "__main__":
    main()
