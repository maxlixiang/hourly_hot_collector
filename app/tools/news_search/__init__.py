# -*- coding: utf-8 -*-
from __future__ import annotations

from app.tools.news_search.query_parser import parse_query_keywords
from app.tools.news_search.sqlite_search import search_news

__all__ = ["parse_query_keywords", "search_news"]
