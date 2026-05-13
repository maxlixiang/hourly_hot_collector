# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
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
EXPERT_REPORT_DIR = "data/analysis/expert_reports"
LLM_REPORT_DIR = "data/analysis/llm_reports"
FILE_PATTERNS = {
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
DEFAULT_TEMPERATURE = 0.4
STRUCTURAL_FIELDS = (
    "cluster_id",
    "rank",
    "event_title",
    "event_type",
    "effective_event_type",
    "heat_score",
    "podcast_candidate_score",
)
LLM_WRITABLE_FIELDS = (
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
    "国家安全例外",
    "外交交易",
    "用贷款买时间",
    "用管道换秩序",
    "常态化扣押",
    "军事护航",
    "事实性部分封锁",
    "战略信誉",
    "彻底逆转",
    "中东能源通道",
)
ALLOWED_EQUIVALENT_TERMS = {
    "hormuz": ("hormuz", "霍尔木兹", "strait of hormuz", "霍尔木兹海峡"),
    "霍尔木兹": ("hormuz", "霍尔木兹", "strait of hormuz", "霍尔木兹海峡"),
    "strait of hormuz": ("hormuz", "霍尔木兹", "strait of hormuz", "霍尔木兹海峡"),
    "霍尔木兹海峡": ("hormuz", "霍尔木兹", "strait of hormuz", "霍尔木兹海峡"),
}
CONSERVATIVE_CUES = (
    "可能",
    "更像",
    "需要关注",
    "取决于",
    "不排除",
    "未必",
    "仍待观察",
)
GAS_DATA_CENTER_SIGNALS = (
    "gas-powered data centers",
    "data centers",
    "天然气",
    "数据中心",
    "greenhouse gases",
)


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


load_dotenv(project_root() / ".env")


def now_dt() -> datetime:
    return datetime.now(ZoneInfo(TIMEZONE))


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def infer_source_type_from_payload(payload: dict[str, Any]) -> str:
    source_type = normalize_text(payload.get("source_type"))
    if source_type not in SUPPORTED_SOURCE_TYPES:
        raise ValueError("Unable to infer source_type from expert report file.")
    return source_type


def find_latest_expert_report(base_dir: Path, source_type: str) -> Path:
    candidates = sorted((base_dir / EXPERT_REPORT_DIR).glob(FILE_PATTERNS[source_type]))
    if not candidates:
        raise FileNotFoundError(f"No expert report file found for source_type={source_type}")
    return candidates[-1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate LLM-style expert writing from expert reports.")
    parser.add_argument("--source-type", choices=SUPPORTED_SOURCE_TYPES, help="Only process the specified source type.")
    parser.add_argument("--input-file", help="Explicit expert report JSON file.")
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


def build_supporting_text(report: dict[str, Any]) -> str:
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
    for item in report.get("expert_context_used", [])[:3]:
        pieces.append(normalize_text(item.get("title")))
        pieces.append(normalize_text(item.get("domain")))
    return "\n".join(piece for piece in pieces if piece)


def is_gas_data_center_topic(report: dict[str, Any]) -> bool:
    haystack = build_supporting_text(report).lower()
    return any(signal.lower() in haystack for signal in GAS_DATA_CENTER_SIGNALS)


def build_prompt(report: dict[str, Any]) -> str:
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

    extra_constraints: list[str] = [
        "不要引入输入中没有明确给出的具体动机、幕后交易、政策交换或行动计划。",
        "不要创造新的具体事实、数字、机制、法律工具或政策工具。",
        "如果是推断，只能使用更保守的表达，例如：可能、更像、需要关注、取决于、不排除。",
        "不要把可能的解释写成已经成立的事实。",
    ]

    if normalize_text(report.get("effective_event_type")) == "geopolitics":
        extra_constraints.extend(
            [
                "对于 geopolitics 事件，优先基于已提供的 key_facts、expert_lens、expert_context_used 写作。",
                "不要补充输入里没有的外交交易、军事升级方案、能源交换机制。",
                "不要自动引入新的国家行为、政策工具或法律名称。",
            ]
        )

    if is_gas_data_center_topic(report):
        extra_constraints.extend(
            [
                "对于天然气供电数据中心相关话题，不要自动扩展到霍尔木兹、中东能源通道、国防生产法、国家安全例外。",
                "这类事件应优先围绕 AI 基础设施扩张、天然气供电与电力约束、碳排放与能源成本、数据中心投资逻辑来写。",
            ]
        )

    constraint_block = "\n".join(f"{index}. {line}" for index, line in enumerate(extra_constraints, start=9))

    return f"""你是资深国际新闻、市场和科技评论写作者。请基于下面的结构化专家分析输入，生成更自然、更像成品的人类专家表达。
要求：
1. 不要机械改写，不要照抄模板句。
2. final_summary 要更短、更自然。
3. expert_analysis 要 1-2 段，真正整合事件事实、专家视角和风险判断。
4. why_it_really_matters 要更有判断力。
5. key_risk 只保留最关键的一条。
6. watch_points 压缩成最重要的 3 条。
7. podcast_hook 要适合播客开头。
8. 只输出 JSON 对象，不要输出解释，不要使用 markdown 代码块，不要补充说明文字。
{constraint_block}

只允许输出以下字段：
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
    base_url = normalize_text(os.getenv(LLM_BASE_URL_ENV, DEFAULT_LLM_BASE_URL))
    model = normalize_text(os.getenv(LLM_MODEL_ENV))
    if not (api_key and base_url and model):
        return None
    return {
        "api_key": api_key,
        "base_url": base_url.rstrip("/"),
        "model": model,
        "timeout": parse_env_int(LLM_TIMEOUT_ENV, DEFAULT_TIMEOUT),
        "temperature": parse_env_float(LLM_TEMPERATURE_ENV, DEFAULT_TEMPERATURE),
    }


def strip_code_fence(text: str) -> str:
    cleaned = normalize_text(text)
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()


def remove_control_characters(text: str) -> str:
    return "".join(char for char in text if char in "\n\r\t" or ord(char) >= 32)


def remove_trailing_commas(text: str) -> str:
    return re.sub(r",(\s*[}\]])", r"\1", text)


def extract_first_complete_json_object(text: str) -> str:
    start = text.find("{")
    if start < 0:
        raise JSONExtractionError("No JSON object start found in LLM response.")

    depth = 0
    in_string = False
    escape = False

    for index in range(start, len(text)):
        char = text[index]

        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]

    raise JSONExtractionError("No complete JSON object found in LLM response.")


def extract_message_content(choice: dict[str, Any]) -> str:
    message = (choice or {}).get("message") or {}
    content = message.get("content")
    if isinstance(content, str):
        return normalize_text(content)
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(normalize_text(item.get("text")))
        return "\n".join(part for part in parts if part).strip()
    return ""


def extract_json_object(text: str) -> dict[str, Any]:
    cleaned = remove_control_characters(strip_code_fence(text))
    if not cleaned:
        raise JSONExtractionError("Empty LLM response.")

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    extracted = extract_first_complete_json_object(cleaned)
    try:
        return json.loads(extracted)
    except json.JSONDecodeError:
        pass

    repaired = remove_trailing_commas(extracted)
    try:
        return json.loads(repaired)
    except json.JSONDecodeError as exc:
        raise JSONParsingError(f"JSON parsing failed after extraction: {exc.msg}") from exc


def call_llm(prompt: str) -> dict[str, Any]:
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
                    "content": "你是严谨、克制、像人类专家写作的分析编辑。禁止把推断写成事实，禁止补充输入中没有的具体机制或政策细节。",
                },
                {
                    "role": "user",
                    "content": prompt,
                },
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


def truncate_watch_points(report: dict[str, Any]) -> list[str]:
    points = compact_list(report.get("watch_points", []), limit=3)
    return points[:3]


def fallback_final_summary(report: dict[str, Any]) -> str:
    event_title = normalize_text(report.get("event_title"))
    event_type = normalize_text(report.get("effective_event_type") or report.get("event_type"))
    if event_type == "geopolitics":
        return f"{event_title} 更像一次牵动安全信号和能源通道预期的地缘风险事件，而不是单纯 headline 波动。"
    if event_type == "markets":
        return f"{event_title} 反映的是市场对风险偏好、估值或宏观约束的重新定价。"
    if event_type == "tech":
        return f"{event_title} 更值得从产业链竞争和资本开支变化，而不是单次产品新闻来理解。"
    return f"{event_title} 已经不只是热点新闻，更值得放到结构性影响框架里观察。"


def fallback_expert_analysis(report: dict[str, Any]) -> str:
    event_summary = normalize_text(report.get("event_summary"))
    core_judgment = normalize_text(report.get("core_judgment"))
    expert_lens = normalize_text(report.get("expert_lens"))
    risk_assessment = normalize_text(report.get("risk_assessment"))
    pieces = [event_summary, core_judgment, expert_lens, risk_assessment]
    return " ".join(piece for piece in pieces if piece)


def fallback_why_it_really_matters(report: dict[str, Any]) -> str:
    why_it_matters = normalize_text(report.get("why_it_matters"))
    market_impact = normalize_text(report.get("market_impact"))
    geopolitical_impact = normalize_text(report.get("geopolitical_impact"))
    return " ".join(piece for piece in [why_it_matters, market_impact, geopolitical_impact] if piece)


def fallback_key_risk(report: dict[str, Any]) -> str:
    event_type = normalize_text(report.get("effective_event_type") or report.get("event_type"))
    if event_type == "geopolitics":
        return "最关键的风险是局势从谈判与威慑博弈，演变成更明确的升级路径。"
    if event_type == "markets":
        return "最关键的风险是市场当前定价过快，但后续基本面或政策信号跟不上。"
    if event_type == "tech":
        return "最关键的风险是 headline 先行，但商业化和部署进展无法验证当前预期。"
    return "最关键的风险是热度先行，但后续事实无法支撑当前叙事。"


def fallback_podcast_hook(report: dict[str, Any]) -> str:
    existing = normalize_text(report.get("podcast_angle"))
    if existing:
        return existing
    event_title = normalize_text(report.get("event_title"))
    return f"这件事真正要看的不是标题本身，而是 {event_title} 背后的结构性变化。"


def build_fallback_output(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "cluster_id": normalize_text(report.get("cluster_id")),
        "rank": report.get("rank"),
        "event_title": normalize_text(report.get("event_title")),
        "event_type": normalize_text(report.get("event_type")),
        "effective_event_type": normalize_text(report.get("effective_event_type") or report.get("event_type")),
        "heat_score": report.get("heat_score"),
        "final_summary": fallback_final_summary(report),
        "expert_analysis": fallback_expert_analysis(report),
        "why_it_really_matters": fallback_why_it_really_matters(report),
        "key_risk": fallback_key_risk(report),
        "uncertainty": normalize_text(report.get("uncertainty")),
        "watch_points": truncate_watch_points(report),
        "podcast_hook": fallback_podcast_hook(report),
        "podcast_candidate_score": report.get("podcast_candidate_score"),
    }


def combined_generated_text(generated: dict[str, Any]) -> str:
    pieces: list[str] = []
    for key in LLM_WRITABLE_FIELDS:
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
    for equivalent in ALLOWED_EQUIVALENT_TERMS.get(phrase, ()):
        if equivalent.lower() in support_text:
            return True
    for equivalents in ALLOWED_EQUIVALENT_TERMS.values():
        if phrase_lower in (item.lower() for item in equivalents):
            return any(item.lower() in support_text for item in equivalents)
    return False


def unsupported_high_risk_phrases(report: dict[str, Any], generated: dict[str, Any]) -> list[str]:
    support_text = build_supporting_text(report).lower()
    output_text = combined_generated_text(generated).lower()
    unsupported: list[str] = []
    for phrase in HIGH_RISK_PHRASES:
        phrase_lower = phrase.lower()
        if phrase_lower in output_text and not term_supported(phrase, support_text):
            unsupported.append(phrase)
    return unsupported


def lacks_conservative_language(generated: dict[str, Any]) -> bool:
    output_text = combined_generated_text(generated)
    return not any(cue in output_text for cue in CONSERVATIVE_CUES)


def guardrail_allows_output(report: dict[str, Any], generated: dict[str, Any]) -> tuple[bool, str]:
    unsupported = unsupported_high_risk_phrases(report, generated)
    if unsupported:
        return False, f"Unsupported high-risk phrases: {', '.join(unsupported)}"

    effective_event_type = normalize_text(report.get("effective_event_type"))
    if effective_event_type == "geopolitics" and lacks_conservative_language(generated):
        return False, "Geopolitics output lacks conservative wording."

    return True, ""


def finalize_output(report: dict[str, Any], generated: dict[str, Any]) -> dict[str, Any]:
    fallback = build_fallback_output(report)
    output: dict[str, Any] = {}

    for key in STRUCTURAL_FIELDS:
        output[key] = fallback[key]

    for key in LLM_WRITABLE_FIELDS:
        fallback_value = fallback[key]
        value = generated.get(key, fallback_value)
        if key == "watch_points":
            points = value if isinstance(value, list) else fallback_value
            output[key] = compact_list(points, limit=3)
            continue
        output[key] = value if normalize_text(value) or isinstance(value, (int, float, list)) else fallback_value

    return output


def generate_item(report: dict[str, Any]) -> tuple[dict[str, Any], str]:
    prompt = build_prompt(report)
    try:
        generated = call_llm(prompt)
        allowed, reason = guardrail_allows_output(report, generated)
        if not allowed:
            raise RuntimeError(f"Guardrail triggered: {reason}")
        print(f"[INFO] Generated with llm for cluster_id={normalize_text(report.get('cluster_id'))}")
        return finalize_output(report, generated), "llm"
    except Exception as exc:
        print(
            f"[WARN] Falling back for cluster_id={normalize_text(report.get('cluster_id'))}: "
            f"{type(exc).__name__}: {exc}"
        )
        return build_fallback_output(report), "fallback"


def build_output_path(base_dir: Path, source_type: str, generated_at: datetime) -> Path:
    output_dir = base_dir / LLM_REPORT_DIR
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir / f"{source_type}_llm_report_{generated_at.strftime('%Y-%m-%d_%H')}.json"


def build_llm_report_for_source_type(
    base_dir: Path,
    source_type: str,
    input_file: Path,
    payload: dict[str, Any],
) -> Path:
    config = llm_config()
    print(f"[INFO] source_type={source_type}")
    print(f"[INFO] LLM configuration detected: {'yes' if config else 'no'}")

    items = []
    llm_count = 0
    for report in payload.get("reports", []):
        generated_item, mode = generate_item(report)
        if mode == "llm":
            llm_count += 1
        items.append(generated_item)

    generated_at = now_dt()
    generation_mode = "llm" if items and llm_count == len(items) else "fallback"
    output_payload = {
        "generated_at": generated_at.strftime("%Y-%m-%d %H:%M:%S %z"),
        "source_type": source_type,
        "source_expert_report_file": str(input_file),
        "report_count": len(items),
        "generation_mode": generation_mode,
        "items": items,
    }
    output_path = build_output_path(base_dir, source_type, generated_at)
    output_path.write_text(json.dumps(output_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[INFO] generation_mode={generation_mode} (llm_items={llm_count}/{len(items)})")
    return output_path


def main() -> None:
    args = parse_args()
    base_dir = project_root()
    targets = resolve_targets(args, base_dir)
    for source_type, input_path, payload in targets:
        output_path = build_llm_report_for_source_type(base_dir, source_type, input_path, payload)
        print(f"[INFO] LLM expert report output: {output_path}")


if __name__ == "__main__":
    main()
