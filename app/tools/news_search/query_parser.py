# -*- coding: utf-8 -*-
from __future__ import annotations

import re
from typing import Any


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
    "一个",
    "关于",
    "事件",
    "做出",
    "总结",
    "整理",
    "内容",
    "观点",
    "相关",
    "一下",
    "the",
    "and",
    "for",
    "with",
    "about",
    "news",
    "analysis",
    "latest",
    "recent",
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


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def parse_query_keywords(query: str, max_keywords: int = 12) -> list[str]:
    """Extract lightweight Chinese terms and English words for local SQLite search."""
    text = normalize_text(query)
    lowered = text.lower()
    keywords: list[str] = []

    for zh_term, synonyms in ZH_ENTITY_SYNONYMS.items():
        if zh_term in text:
            keywords.extend(synonyms)

    for token in re.findall(r"[a-zA-Z][a-zA-Z0-9\.\-]{1,}", lowered):
        if token not in QUERY_STOPWORDS and len(token) >= 2:
            keywords.append(token)

    for token in re.findall(r"[\u4e00-\u9fff]{2,}", text):
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
        if len(result) >= max_keywords:
            break
    return result
