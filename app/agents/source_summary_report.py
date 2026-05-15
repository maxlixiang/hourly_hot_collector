# -*- coding: utf-8 -*-
from __future__ import annotations

import html
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

import requests
from zoneinfo import ZoneInfo

from app.agents.llm_expert_writer import extract_json_object, extract_message_content, llm_config


TIMEZONE = "Asia/Shanghai"
SOURCE_REPORT_DIR = "data/agent/source_reports"
MAX_ARTICLES_PER_MEDIA = 8
MAX_EXCERPT_CHARS = 900
SOURCE_SUMMARY_TEMPERATURE = 0.15


def now_dt() -> datetime:
    return datetime.now(ZoneInfo(TIMEZONE))


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def truncate_text(value: Any, limit: int) -> str:
    text = normalize_text(value)
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def safe_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def source_distribution_text(distribution: list[dict[str, Any]]) -> str:
    if not distribution:
        return "无"
    return "，".join(
        f"{normalize_text(item.get('source_name')) or 'Unknown source'} {int(item.get('article_count') or 0)} 条"
        for item in distribution
    )


def article_excerpt(source: dict[str, Any]) -> str:
    return truncate_text(
        source.get("source_excerpt")
        or source.get("summary")
        or normalize_text(source.get("title")),
        MAX_EXCERPT_CHARS,
    )


def sources_with_citations(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cited_sources: list[dict[str, Any]] = []
    for index, source in enumerate(sources, start=1):
        cited = dict(source)
        cited["citation_id"] = f"S{index}"
        cited["source_excerpt"] = article_excerpt(source)
        cited_sources.append(cited)
    return cited_sources


def group_sources_by_media(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for source in sources:
        source_name = normalize_text(source.get("source_name")) or "Unknown source"
        grouped[source_name].append(source)

    groups: list[dict[str, Any]] = []
    for source_name, items in sorted(grouped.items(), key=lambda item: (-len(item[1]), item[0])):
        groups.append(
            {
                "source_name": source_name,
                "article_count": len(items),
                "articles": [
                    {
                        "citation_id": normalize_text(article.get("citation_id")),
                        "title": normalize_text(article.get("title")),
                        "url": normalize_text(article.get("url")),
                        "published_time_beijing": normalize_text(article.get("published_time_beijing")),
                        "content_fetch_status": normalize_text(article.get("content_fetch_status")),
                        "content_quality": normalize_text(article.get("content_quality")),
                        "excerpt": article_excerpt(article),
                    }
                    for article in items[:MAX_ARTICLES_PER_MEDIA]
                ],
            }
        )
    return groups


def build_prompt(item: dict[str, Any], cited_sources: list[dict[str, Any]]) -> str:
    prompt_payload = {
        "event_title": normalize_text(item.get("event_title")),
        "all_source_distribution": item.get("source_distribution", []),
        "analysis_source_distribution": item.get("analysis_source_distribution", []),
        "excluded_source_distribution": item.get("excluded_source_distribution", []),
        "media_groups": group_sources_by_media(cited_sources),
    }
    return f"""你是普通新闻事实整理员，不是专家分析师。请只根据输入中的原文摘录、标题、来源和发布时间，完成非专家模式的内容整理。

硬性要求：
1. 只输出 JSON 对象，不要 markdown，不要解释。
2. 不要加入专家判断、预测、投资建议、地缘战略推断、动机推断或输入中没有的新事实。
3. 每一个事实性判断都必须引用 citation_id。不要输出没有引用的事实断言。
4. 如果多个来源共同确认同一事实，citations 应包含多个 citation_id。
5. 如果信息不足，要写进 information_gaps，不要自行补全。
6. 描述媒体时使用“报道重点是”“提到”“补充”，不要把普通报道写成“认为”，除非输入明确是评论/观点。
7. 输出字段必须符合下面 schema：

{{
  "event_overview": {{"text": "...", "citations": ["S1"]}},
  "media_summaries": [
    {{
      "source_name": "...",
      "summary": "...",
      "citations": ["S1"],
      "key_points": [{{"text": "...", "citations": ["S1"]}}],
      "unique_details": [{{"text": "...", "citations": ["S2"]}}]
    }}
  ],
  "common_facts": [{{"text": "...", "citations": ["S1", "S2"]}}],
  "differences_and_additions": [{{"text": "...", "citations": ["S1", "S3"]}}],
  "information_gaps": [{{"text": "...", "citations": ["S1"]}}]
}}

输入数据：
{json.dumps(prompt_payload, ensure_ascii=False, indent=2)}
"""


def call_source_summary_llm(prompt: str) -> dict[str, Any]:
    config = llm_config()
    if not config:
        raise RuntimeError("LLM configuration is not available.")

    response = requests.post(
        f"{config['base_url']}/chat/completions",
        headers={
            "Authorization": f"Bearer {config['api_key']}",
            "Content-Type": "application/json",
        },
        json={
            "model": config["model"],
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是严谨的普通新闻事实整理员。只根据输入原文摘录归纳，所有事实都要引用 citation_id。"
                        "不要使用专家视角，不要补充外部知识，不要把推断写成事实。"
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": min(float(config.get("temperature") or SOURCE_SUMMARY_TEMPERATURE), SOURCE_SUMMARY_TEMPERATURE),
            "stream": False,
        },
        timeout=int(config["timeout"]),
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


def normalize_citations(value: Any, valid_citations: set[str]) -> list[str]:
    citations: list[str] = []
    for item in safe_list(value):
        citation = normalize_text(item)
        if citation in valid_citations and citation not in citations:
            citations.append(citation)
    return citations


def sanitize_claim(value: Any, valid_citations: set[str]) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    text = normalize_text(value.get("text"))
    citations = normalize_citations(value.get("citations"), valid_citations)
    if not text or not citations:
        return None
    return {"text": text, "citations": citations}


def sanitize_report(generated: dict[str, Any], valid_citations: set[str]) -> dict[str, Any]:
    overview = sanitize_claim(generated.get("event_overview"), valid_citations)
    media_summaries: list[dict[str, Any]] = []
    for item in safe_list(generated.get("media_summaries")):
        if not isinstance(item, dict):
            continue
        source_name = normalize_text(item.get("source_name"))
        summary = normalize_text(item.get("summary"))
        citations = normalize_citations(item.get("citations"), valid_citations)
        key_points = [
            claim for claim in (sanitize_claim(claim, valid_citations) for claim in safe_list(item.get("key_points"))) if claim
        ]
        unique_details = [
            claim
            for claim in (sanitize_claim(claim, valid_citations) for claim in safe_list(item.get("unique_details")))
            if claim
        ]
        if source_name and summary and citations:
            media_summaries.append(
                {
                    "source_name": source_name,
                    "summary": summary,
                    "citations": citations,
                    "key_points": key_points,
                    "unique_details": unique_details,
                }
            )

    return {
        "event_overview": overview,
        "media_summaries": media_summaries,
        "common_facts": [
            claim
            for claim in (sanitize_claim(claim, valid_citations) for claim in safe_list(generated.get("common_facts")))
            if claim
        ],
        "differences_and_additions": [
            claim
            for claim in (
                sanitize_claim(claim, valid_citations) for claim in safe_list(generated.get("differences_and_additions"))
            )
            if claim
        ],
        "information_gaps": [
            claim
            for claim in (sanitize_claim(claim, valid_citations) for claim in safe_list(generated.get("information_gaps")))
            if claim
        ],
    }


def fallback_report(item: dict[str, Any], cited_sources: list[dict[str, Any]]) -> dict[str, Any]:
    media_summaries: list[dict[str, Any]] = []
    for group in group_sources_by_media(cited_sources):
        citations = [article["citation_id"] for article in group["articles"] if article.get("citation_id")]
        summary_parts = [
            truncate_text(article.get("excerpt"), 180)
            for article in group["articles"][:3]
            if normalize_text(article.get("excerpt"))
        ]
        if not citations or not summary_parts:
            continue
        media_summaries.append(
            {
                "source_name": group["source_name"],
                "summary": "；".join(summary_parts),
                "citations": citations[:3],
                "key_points": [{"text": part, "citations": [citations[index]]} for index, part in enumerate(summary_parts[:3])],
                "unique_details": [],
            }
        )

    first_citation = normalize_text(cited_sources[0].get("citation_id")) if cited_sources else ""
    overview = {
        "text": normalize_text(item.get("event_title")) or "本组新闻缺少可整理标题。",
        "citations": [first_citation] if first_citation else [],
    }
    return {
        "event_overview": overview,
        "media_summaries": media_summaries,
        "common_facts": [],
        "differences_and_additions": [],
        "information_gaps": [
            {
                "text": "LLM 未配置或调用失败，本报告使用原文摘录做规则化整理，未生成跨媒体共同事实判断。",
                "citations": [first_citation] if first_citation else [],
            }
        ]
        if first_citation
        else [],
    }


def citation_lookup(cited_sources: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {normalize_text(source.get("citation_id")): source for source in cited_sources}


def citation_links(citations: list[str], lookup: dict[str, dict[str, Any]]) -> str:
    links: list[str] = []
    for citation in citations:
        source = lookup.get(citation, {})
        url = normalize_text(source.get("url"))
        title = html.escape(normalize_text(source.get("title")) or citation)
        if url:
            links.append(f'<a href="{html.escape(url)}" target="_blank" rel="noopener">{html.escape(citation)}</a>')
        else:
            links.append(html.escape(citation))
    return " ".join(links)


def render_claim_list(title: str, claims: list[dict[str, Any]], lookup: dict[str, dict[str, Any]]) -> str:
    if not claims:
        return f"<section><h3>{html.escape(title)}</h3><p class=\"muted\">暂无可引用结论。</p></section>"
    items = []
    for claim in claims:
        items.append(
            "<li>"
            f"{html.escape(normalize_text(claim.get('text')))} "
            f"<span class=\"cite\">{citation_links(safe_list(claim.get('citations')), lookup)}</span>"
            "</li>"
        )
    return f"<section><h3>{html.escape(title)}</h3><ul>{''.join(items)}</ul></section>"


def render_event_html(item_report: dict[str, Any]) -> str:
    item = item_report["item"]
    report = item_report["report"]
    cited_sources = item_report["cited_sources"]
    lookup = citation_lookup(cited_sources)
    overview = report.get("event_overview") or {}

    media_sections = []
    for media in safe_list(report.get("media_summaries")):
        key_points = "".join(
            "<li>"
            f"{html.escape(normalize_text(point.get('text')))} "
            f"<span class=\"cite\">{citation_links(safe_list(point.get('citations')), lookup)}</span>"
            "</li>"
            for point in safe_list(media.get("key_points"))
        )
        unique_details = "".join(
            "<li>"
            f"{html.escape(normalize_text(point.get('text')))} "
            f"<span class=\"cite\">{citation_links(safe_list(point.get('citations')), lookup)}</span>"
            "</li>"
            for point in safe_list(media.get("unique_details"))
        )
        media_sections.append(
            "<section class=\"media\">"
            f"<h3>{html.escape(normalize_text(media.get('source_name')))}</h3>"
            f"<p>{html.escape(normalize_text(media.get('summary')))} "
            f"<span class=\"cite\">{citation_links(safe_list(media.get('citations')), lookup)}</span></p>"
            f"<h4>报道重点</h4><ul>{key_points or '<li class=\"muted\">暂无。</li>'}</ul>"
            f"<h4>独有补充</h4><ul>{unique_details or '<li class=\"muted\">暂无。</li>'}</ul>"
            "</section>"
        )

    source_rows = []
    for source in cited_sources:
        url = normalize_text(source.get("url"))
        title = html.escape(normalize_text(source.get("title")))
        title_html = f'<a href="{html.escape(url)}" target="_blank" rel="noopener">{title}</a>' if url else title
        source_rows.append(
            "<tr>"
            f"<td>{html.escape(normalize_text(source.get('citation_id')))}</td>"
            f"<td>{html.escape(normalize_text(source.get('source_name')))}</td>"
            f"<td>{title_html}</td>"
            f"<td>{html.escape(normalize_text(source.get('published_time_beijing')))}</td>"
            f"<td>{html.escape(normalize_text(source.get('content_fetch_status')))}</td>"
            "</tr>"
        )

    return (
        "<article>"
        f"<h2>新闻{html.escape(str(item.get('display_index')))}：{html.escape(normalize_text(item.get('event_title')))}</h2>"
        "<section>"
        "<h3>事件总览</h3>"
        f"<p>{html.escape(normalize_text(overview.get('text')))} "
        f"<span class=\"cite\">{citation_links(safe_list(overview.get('citations')), lookup)}</span></p>"
        f"<p class=\"muted\">完整来源：{html.escape(source_distribution_text(item.get('source_distribution', [])))}。</p>"
        f"<p class=\"muted\">进入内容分析：{html.escape(source_distribution_text(item.get('analysis_source_distribution', [])))}。</p>"
        f"<p class=\"muted\">排除来源：{html.escape(source_distribution_text(item.get('excluded_source_distribution', [])))}。</p>"
        "</section>"
        f"{''.join(media_sections)}"
        f"{render_claim_list('共同事实', safe_list(report.get('common_facts')), lookup)}"
        f"{render_claim_list('差异与补充', safe_list(report.get('differences_and_additions')), lookup)}"
        f"{render_claim_list('信息缺口', safe_list(report.get('information_gaps')), lookup)}"
        "<section><h3>原文引用表</h3>"
        "<table><thead><tr><th>ID</th><th>来源</th><th>标题</th><th>时间</th><th>读取状态</th></tr></thead>"
        f"<tbody>{''.join(source_rows)}</tbody></table></section>"
        "</article>"
    )


def render_html_report(payload: dict[str, Any]) -> str:
    event_sections = "\n".join(render_event_html(item_report) for item_report in payload.get("items", []))
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>新闻内容整理报告</title>
  <style>
    body {{ margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; color: #202124; background: #f6f7f9; }}
    main {{ max-width: 1120px; margin: 0 auto; padding: 32px 20px 56px; }}
    article, header {{ background: #fff; border: 1px solid #dde1e6; border-radius: 8px; padding: 22px; margin-bottom: 18px; }}
    h1, h2, h3, h4 {{ margin: 0 0 12px; line-height: 1.25; }}
    h1 {{ font-size: 28px; }}
    h2 {{ font-size: 22px; border-bottom: 1px solid #eceff3; padding-bottom: 12px; }}
    h3 {{ font-size: 18px; margin-top: 18px; }}
    h4 {{ font-size: 14px; margin-top: 14px; color: #4b5563; }}
    p, li {{ font-size: 15px; line-height: 1.75; }}
    section {{ margin-top: 16px; }}
    .media {{ border-top: 1px solid #eceff3; padding-top: 14px; }}
    .muted {{ color: #667085; }}
    .cite {{ white-space: nowrap; color: #475467; font-size: 13px; }}
    .cite a {{ color: #2563eb; text-decoration: none; margin-left: 4px; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
    th, td {{ border-top: 1px solid #eceff3; padding: 9px 8px; text-align: left; vertical-align: top; }}
    th {{ color: #475467; background: #fafafa; }}
    a {{ color: #2563eb; }}
  </style>
</head>
<body>
<main>
  <header>
    <h1>新闻内容整理报告</h1>
    <p class="muted">生成时间：{html.escape(normalize_text(payload.get('generated_at')))}。模式：普通新闻事实整理，非专家分析。LLM temperature ≤ {SOURCE_SUMMARY_TEMPERATURE}。</p>
    <p class="muted">防幻觉规则：所有事实判断要求引用原文 citation_id；无法确认的信息进入“信息缺口”。</p>
  </header>
  {event_sections}
</main>
</body>
</html>
"""


def build_item_report(item: dict[str, Any]) -> dict[str, Any]:
    sources = safe_list(item.get("sources"))
    cited_sources = sources_with_citations(sources)
    valid_citations = {normalize_text(source.get("citation_id")) for source in cited_sources}
    mode = "fallback"
    error = ""
    try:
        generated = call_source_summary_llm(build_prompt(item, cited_sources))
        report = sanitize_report(generated, valid_citations)
        if not report.get("event_overview") or not report.get("media_summaries"):
            raise RuntimeError("LLM report has insufficient cited content.")
        mode = "llm"
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        report = fallback_report(item, cited_sources)

    return {
        "item": {
            "display_index": item.get("display_index"),
            "source_type": item.get("source_type"),
            "cluster_id": item.get("cluster_id"),
            "event_title": item.get("event_title"),
            "source_distribution": item.get("source_distribution", []),
            "analysis_source_distribution": item.get("analysis_source_distribution", []),
            "excluded_source_distribution": item.get("excluded_source_distribution", []),
        },
        "generation_mode": mode,
        "generation_error": error,
        "cited_sources": cited_sources,
        "report": report,
    }


def write_source_summary_report(
    base_dir: Path,
    *,
    query: str,
    items: list[dict[str, Any]],
) -> dict[str, Any]:
    generated_at = now_dt()
    output_dir = base_dir / SOURCE_REPORT_DIR
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = generated_at.strftime("%Y-%m-%d_%H%M%S")
    json_path = output_dir / f"source_summary_{stamp}.json"
    html_path = output_dir / f"source_summary_{stamp}.html"

    item_reports = [build_item_report(item) for item in items if item.get("sources")]
    payload = {
        "generated_at": generated_at.strftime("%Y-%m-%d %H:%M:%S %z"),
        "query": query,
        "writer_mode": "news_source_summary",
        "temperature_max": SOURCE_SUMMARY_TEMPERATURE,
        "generation_mode": "llm"
        if item_reports and all(item.get("generation_mode") == "llm" for item in item_reports)
        else "fallback",
        "items": item_reports,
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    html_path.write_text(render_html_report(payload), encoding="utf-8")
    return {
        "json_path": str(json_path),
        "html_path": str(html_path),
        "payload": payload,
    }
