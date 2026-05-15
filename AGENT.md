# AGENT.md

This file is for future Codex agents working on this repository.

The project is a local news collection, hot topic discovery, RAG retrieval, and expert-analysis pipeline. It is intentionally a single-repo, single-machine Python project. Do not turn it into a web service or a microservice stack unless the user explicitly asks.

## Current Mental Model

The main data flow is:

```text
NewsNow + RSS
  -> hourly_hot_collector.py
  -> SQLite news_items
  -> hot_topic_pipeline.py
  -> hot_clusters JSON
  -> cluster_context_builder
  -> basic_analysis_agent
  -> retriever
  -> expert_agent
  -> llm_expert_writer
```

Knowledge data flow:

```text
data/knowledge/sources/**/*.txt
  -> scripts/run_knowledge_ingest.py
  -> data/knowledge/processed/documents.jsonl
  -> data/knowledge/processed/chunks.jsonl
  -> scripts/run_retriever.py
```

Knowledge evolution flow:

```text
documents.jsonl + chunks.jsonl
  -> scripts/run_knowledge_evolution.py
  -> data/knowledge/evolution/viewpoints.jsonl
  -> data/knowledge/evolution/view_evolution.jsonl
```

Interactive agent flow:

```text
user query
  -> scripts/run_agents.py
  -> task_planner
  -> optional prerequisite pipeline scheduler
  -> agent_orchestrator
  -> response JSON under data/agent/responses or .agent_runtime/responses
```

The first agent version intentionally supports only three task types:

- `hot_news_query`
- `source_summary_request`
- `expert_topic_analysis`

## Important Entry Points

Root compatibility wrappers:

- `hourly_hot_collector.py`: runs NewsNow + RSS collection.
- `hot_topic_pipeline.py`: runs hot topic clustering from SQLite.
- `cluster_context_builder.py`: root wrapper for context builder.
- `db.py`: root wrapper for `app/storage/db.py`.

Rules for root wrappers:

- Do not add business logic to root wrappers.
- New code belongs under `app/`.
- New runnable entry points should prefer `scripts/`.
- Keep wrappers thin and compatible because Docker and older commands may still call them.

Preferred script entry points:

- `scripts/run_collector.py`
- `scripts/run_hot_pipeline.py`
- `scripts/run_context_builder.py`
- `scripts/run_basic_agent.py`
- `scripts/run_retriever.py`
- `scripts/run_expert_agent.py`
- `scripts/run_llm_expert_writer.py`
- `scripts/run_knowledge_ingest.py`
- `scripts/run_knowledge_evolution.py`
- `scripts/run_agents.py`

## Module Responsibilities

`app/collectors/`

- `collector_common.py`: shared config, paths, time/text helpers, run status helpers.
- `newsnow_collector.py`: NewsNow source fetching, markdown/raw/SQLite standardization.
- `rss_collector.py`: RSS source loading, incremental filtering, markdown/raw/SQLite standardization.

Collector scheduling:

- Default `RUN_MINUTE` is `58`.
- Default `RUN_IMMEDIATELY` is `false`.
- A run at `13:58` writes `*_YYYY-MM-DD_13.*`.
- RSS items published after `13:58` and before `14:00` are intentionally collected in the next `14:58` run and written to `*_14.*`.
- RSS collection is intentionally incremental. `rss_collector.py` uses the previous `success` or `partial` fetch run's `finished_at` as the next window start, falling back to the previous hour only when there is no successful prior run. Keep this behavior unless the user explicitly asks to change RSS collection semantics.
- Do not re-enable immediate runs by default; same-hour immediate runs can overwrite same-hour Markdown/raw files.

`app/storage/`

- `db.py`: SQLite schema and insert/query helpers.
- `sqlite_reader.py`, `file_store.py`: currently light/placeholder support modules.

`app/pipelines/`

- `hot_topic_pipeline.py`: SQLite -> dedup -> quality filter -> embeddings -> clustering -> hot clusters.
- `cluster_context_builder.py`: hot clusters -> article ids -> SQLite lookup -> cluster context.
- `dedup.py`, `clustering.py`, `quality_filters.py`: extraction targets or helper modules. Follow existing usage before expanding them.

`app/agents/`

- `agent_orchestrator.py`: lightweight conversation orchestrator. It auto-runs prerequisite pipeline scripts for each query, then dispatches the request by task type.
- `task_planner.py`: rule-based planner for `hot_news_query`, `source_summary_request`, and `expert_topic_analysis`.
- `article_reader.py`: compatibility wrapper. Do not add new extraction logic here.
- `memory_store.py`: lightweight file-backed memory for latest hot list and recent interactions.
- `reflection_checker.py`: small self-check helpers for source summary and expert-analysis responses.
- `source_summary_report.py`: non-expert LLM/HTML writer for `source_summary_request`; keeps citations back to source excerpts.
- `basic_analysis_agent.py`: rule-based analysis from cluster context.
- `expert_agent.py`: rule/template expert report from context + analysis + retrieved context.
- `llm_expert_writer.py`: final expression layer. Uses OpenAI-compatible chat completions if configured, otherwise fallback.
- `geopolitics_agent.py`, `markets_agent.py`, `tech_agent.py`, `synthesis_agent.py`: future placeholders.

`app/rag/`

- `knowledge_ingest.py`: txt knowledge cards -> documents/chunks JSONL.
- `retriever.py`: basic analysis -> keyword retrieval over chunks.
- `knowledge_evolution.py`: offline viewpoint extraction and evolution records.
- `knowledge_store.py`: knowledge path constants.
- `index_builder.py`: future placeholder.

`app/tools/`

- `article_reader/`: standalone URL-to-content tool used by the Agent source-summary flow.
- `article_reader/reader.py`: main implementation. It tries direct HTML extraction with trafilatura first, then newspaper4k, Jina AI Reader, and local HTML extraction before letting the caller fall back to SQLite/RSS summaries.
- `article_reader/schemas.py`: lightweight result schema types.
- `article_reader/source_policies.py`: RSS source article-reading policy loader. It supports `enabled`, `rss_content`, and `disabled` source behavior.
- `news_search/`: standalone SQLite topic search tool for recent `news_items`. It filters by query keywords, time window, and `source_type`, then ranks title hits before summary hits and newer items.
- `news_search/sqlite_search.py`: exposes the stable interface `search_news(base_dir, query, window_hours=24, source_type="mixed", limit=20)`.
- `news_search/query_parser.py`: lightweight Chinese continuous-term and English-word keyword extraction with simple stopword filtering.
- `news_search/schemas.py`: lightweight result/query schema types.

Future article extraction work should stay inside `app/tools/article_reader/`. The main Agent should only call the stable interface:

```python
from app.tools.article_reader import read_article
```

For SQLite topic fallback search, the main Agent should call:

```python
from app.tools.news_search import search_news
```

`config/`

- `rss_sources.txt`: RSS source list.
- `rss_source_policies.txt`: per-RSS-source article-reading policy. Keep blocked/paywalled but editorially useful feeds in `rss_sources.txt`, then mark article reading as `disabled` here.
- `newsnow_frequency_words.txt`: NewsNow quality filtering words.
- `newsnow_event_rules.txt`: NewsNow event-score rules.
- `basic_analysis_event_rules.txt`: event classification and reliable-source rules.
- `collector.example.env`, `pipeline.example.env`: example env files only. They are not automatically loaded as real runtime config.

## Runtime Data

Important output locations:

- `data/db/data_hub.db`
- `data/markdown/newsnow/`
- `data/markdown/rss/`
- `data/raw/newsnow/`
- `data/raw/rss/`
- `data/hot/newsnow/`
- `data/hot/rss/`
- `data/analysis/context/`
- `data/analysis/reports/`
- `data/analysis/retrieved_context/`
- `data/analysis/expert_reports/`
- `data/analysis/llm_reports/`
- `data/knowledge/processed/`
- `data/knowledge/evolution/`
- `data/agent/session_state.json`
- `data/agent/responses/`
- `data/agent/article_cache/`
- `.agent_runtime/` fallback files when `data/agent/` is not writable
- `logs/failed_sources.log`

Generated outputs are usually ignored by git, except curated knowledge source txt files under `data/knowledge/sources/`.

## Interactive Agent Notes

`scripts/run_agents.py` is now the user-facing agent entry point.

Supported examples:

```bash
python scripts/run_agents.py --query "告诉我过去24小时的10条热点新闻"
python scripts/run_agents.py --query "请对1，2做内容整理和总结"
python scripts/run_agents.py --query "从专家的角度，分析过去一周关于以色列和伊朗战争的相关新闻"
```

Useful flags:

- `--no-auto-pipeline`: do not run prerequisite scripts.
- `--force-pipeline`: retained for CLI compatibility; automatic pipeline mode already regenerates prerequisite outputs on each query.
- `--skip-llm`: skip the LLM writer during automatic pipeline preparation.
- `--json`: print the full structured response.

Prerequisite scheduling:

- `source_summary_request` uses the previous hot-news session state and does not run the full pipeline automatically.
- Expert capability is intentionally opt-in. The planner should only infer `expert_topic_analysis` when the user query explicitly contains `专家`; generic words like “分析”, “预测”, “影响”, “判断”, or “怎么看” must stay on the ordinary news path unless `--task-type expert_topic_analysis` is provided.
- Hot-news queries support three return-stage domain filters: `finance`, `geopolitics`, and `tech_ai`. Keep planner trigger words intentionally narrow: `财经` -> `finance`; `政治`, `国际`, `地缘`, `地缘政治` -> `geopolitics`; `科技`, `AI`, `ai` -> `tech_ai`. Richer domain keywords may still be used inside the return-stage cluster scoring logic. Domain filtering must happen after full hot-topic generation; do not filter collection or clustering inputs for these ordinary domain requests.
- `hot_news_query` regenerates hot topic, context, basic analysis, and `run_llm_expert_writer --mode news` on each query unless `--no-auto-pipeline` is set. It must stay source-grounded and must not add expert interpretation.
- `expert_topic_analysis` regenerates hot topic, context, basic analysis, knowledge ingestion, retriever, expert report, and `run_llm_expert_writer --mode expert`, then searches the newly generated expert/LLM reports unless `--no-auto-pipeline` is set.
- User-requested time windows flow into `run_hot_pipeline.py`. For example, `告诉我过去24小时的10条热点新闻` becomes `python scripts/run_hot_pipeline.py --window-hours 24`.
- `agent_orchestrator.py` must pass the current run's hot cluster files to `run_context_builder.py --input-file ...`; do not let context building guess hot files via glob ordering. This prevents mixing old 5h/24h hot outputs into a 3h request.
- Hot topic windows use the last N complete hours, not a rolling window to the current minute. If current time is 18:42 and the user asks for the past 3 hours, analyze `[15:00, 18:00)`, i.e. the complete 15, 16, and 17 o'clock hours.
- Hot cluster outputs record `analysis_window_hours`, `analysis_window`, and `data_coverage`. If the database does not cover the full requested complete-hour window, the final Agent answer should warn the user, e.g. “当前数据库没有完整的最近 24 个完整小时数据，现有数据库约覆盖过去 5 小时，下面仅根据这部分数据给出分析结果。”
- `cluster_context_builder.py` validates hot file `source_type`. When SQLite article ids point to a different source/title than the hot cluster embedded snapshot, it uses the hot snapshot and records/prints a warning instead of silently attaching unrelated articles.
- Token cost: only `run_llm_expert_writer` calls an OpenAI-compatible chat completions endpoint, and only when `LLM_EXPERT_WRITER_API_KEY`, `LLM_EXPERT_WRITER_BASE_URL`, and `LLM_EXPERT_WRITER_MODEL` are configured. `--mode news` is ordinary source-grounded news briefing; `--mode expert` is the expert-analysis expression layer. `hot` uses local sentence-transformers embeddings and sklearn clustering; context/basic/knowledge/retrieved/expert are local processing steps.

Source-summary behavior:

```text
RSS source policy
  -> enabled:
       article URL
         -> trafilatura
         -> newspaper4k
         -> Jina AI Reader: https://r.jina.ai/{article URL}
         -> local HTML parser
         -> SQLite/RSS summary or title fallback
  -> rss_content:
       use RSS item content:encoded directly, without visiting the article URL
  -> disabled:
       skip article reading and use RSS/SQLite summary or title
```

Notes:

- `trafilatura` is now preferred. Sampling across the current RSS set showed it is usually cleaner than Jina Reader for body extraction. Jina remains a fallback layer.
- `trafilatura` and `newspaper4k` are optional extractor layers. They are listed in requirements, but the code still skips them safely if unavailable.
- `article_reader.py` only caches successful reads. Failed network/403 attempts and policy-disabled reads are not cached as full text.
- Cache version is controlled by `EXTRACTOR_VERSION`; bump it when changing extraction semantics.
- `content_fetch_status` should distinguish full text from summary-only fallbacks. Bloomberg/NYT/MarketWatch/WSJ/Economist-style blocked pages should remain useful RSS signals, but should not be presented as full text.
- Fox News World currently uses `rss_content` because its RSS items include usable body text in `content:encoded`.
- For `source_summary_request` such as `请对1做内容整理`, the Agent should read every article link in the selected hot cluster. Do not cap this at 5 sources and do not deduplicate by media/source name; if one media outlet has multiple articles in the cluster, keep all of them. Before content analysis, filter out RSS sources whose `config/rss_source_policies.txt` policy is `article_reading=disabled`. These sources remain useful for hot-topic discovery and heat scoring, but must not enter the body-text analysis. The answer should include a transparent exclusion note, e.g. `内容分析已排除 34 条无法读取正文的来源：Bloomberg Markets 18 条，NYT World 16 条；这些来源仅用于热点发现和热度判断。`
- After source reading, `source_summary_request` should write a non-expert source report to `data/agent/source_reports/source_summary_*.html` and JSON. The report groups content by media source, extracts common facts, differences/additions, and information gaps. Use low-temperature LLM generation with a maximum temperature of `0.15`; every factual claim in the LLM output must carry citation ids that point back to the read article excerpts. If LLM config is absent or citation validation fails, use fallback output rather than uncited prose.
- The final answer must stay objective for `source_summary_request`: no expert interpretation, no subjective embellishment.
- `reflection_checker.py` may append self-check notes when source reads fail or summaries fall back to local data.

Recommended new-thread prompt for focused development:

```text
优化 article_reader 工具。
请先阅读 AGENT.md 和 app/tools/article_reader/，
只围绕正文提取模块开发，不要改主 Agent 流程。
```

## LLM Writer Notes

`app/agents/llm_expert_writer.py` loads the root `.env` with `python-dotenv`.

Expected env vars:

```env
LLM_EXPERT_WRITER_API_KEY=
LLM_EXPERT_WRITER_BASE_URL=
LLM_EXPERT_WRITER_MODEL=
LLM_EXPERT_WRITER_TIMEOUT=90
LLM_EXPERT_WRITER_TEMPERATURE=0.4
```

It uses OpenAI-compatible `/chat/completions`.

Important guardrails:

- `llm_expert_writer.py` has two modes. `--mode news` reads cluster context plus basic analysis and writes source-grounded news briefing fields such as `final_summary`, `source_grounded_summary`, `known_facts`, and `uncertainties`; it must not populate expert interpretation. `--mode expert` reads `expert_report` and may write expert-analysis fields.
- Structural fields must always inherit from the upstream source item.
- In expert mode, the LLM may only write narrative fields such as `final_summary`, `expert_analysis`, `why_it_really_matters`, `key_risk`, `uncertainty`, `watch_points`, and `podcast_hook`.
- If the API fails, returns empty content, returns malformed JSON, or trips guardrails, fallback output is used.

Do not loosen these guardrails casually. The user cares about traceability and schema stability.

## Knowledge Policy

The main project does not generate knowledge cards from YouTube or webpages. That path was intentionally removed from the main repo.

Keep this boundary:

```text
External tools may generate txt knowledge cards.
This project only reads data/knowledge/sources/**/*.txt,
then ingests and retrieves them.
```

Do not reintroduce YouTube transcript download, Whisper, NotebookLM, channel monitoring, or LLM summary generation into this repo unless the user explicitly reverses that architecture decision.

## Common Commands

Run collection:

```bash
python scripts/run_collector.py
```

Run hot topic discovery:

```bash
python scripts/run_hot_pipeline.py
```

Build cluster context:

```bash
python scripts/run_context_builder.py
```

Run rule-based analysis:

```bash
python scripts/run_basic_agent.py
```

Ingest knowledge:

```bash
python scripts/run_knowledge_ingest.py
```

Retrieve knowledge:

```bash
python scripts/run_retriever.py
```

Run expert reports:

```bash
python scripts/run_expert_agent.py
```

Run LLM writer:

```bash
python scripts/run_llm_expert_writer.py
```

Run knowledge evolution:

```bash
python scripts/run_knowledge_evolution.py
```

Run the interactive agent:

```bash
python scripts/run_agents.py --query "告诉我过去24小时的10条热点新闻"
```

## Verification

For small code changes, at minimum run targeted compile checks:

```bash
python -m py_compile path/to/changed_file.py
```

For pipeline-related changes, prefer running the relevant script directly. Many scripts are designed to degrade gracefully when data is missing.

Useful broader check:

```bash
python -m py_compile app/agents/basic_analysis_agent.py app/agents/expert_agent.py app/agents/llm_expert_writer.py app/pipelines/hot_topic_pipeline.py app/pipelines/cluster_context_builder.py app/rag/knowledge_ingest.py app/rag/retriever.py app/rag/knowledge_evolution.py
```

On Windows, if `__pycache__` write permissions fail, use:

```bash
$env:PYTHONDONTWRITEBYTECODE='1'; python -m py_compile path/to/file.py
```

## Development Rules

- Keep changes small and local.
- Do not change SQLite schema unless the user explicitly asks.
- Do not modify collector behavior while working on agents/RAG, unless the request specifically targets collection.
- Do not modify LLM prompts and retrieval rules in the same change unless required.
- Keep root wrappers working.
- Preserve output schemas unless the user explicitly asks for schema changes.
- Do not commit generated database, logs, or large runtime outputs unless the user explicitly asks.
- Do not commit `data/agent/article_cache/`, `data/agent/responses/*.json`, or `.agent_runtime/`.
- Use `apply_patch` for manual edits.

## Known Caveats

- Some historical knowledge txt files may contain mojibake from previous imports. Do not silently rewrite them unless the task is specifically about data cleanup.
- `config/*.example.env` files are examples. Runtime values should be in `.env` or real environment variables.
- `generation_mode` in LLM reports currently reports `llm` only when all items in that run used LLM successfully; mixed fallback cases report `fallback`.
- The project is still evolving from root-level scripts to `app/` modules. Avoid large reshuffles unless the user asks for architecture work.

---

# AGENT.md 中文版

这份文件是给未来接手本仓库的 Codex / 开发助手看的。

这个项目是一个本地运行的新闻采集、热点发现、RAG 检索和专家分析 pipeline。它目前应保持为单仓库、单机 Python 项目。除非用户明确要求，不要把它改成 Web 服务或微服务架构。

## 当前心智模型

主数据流是：

```text
NewsNow + RSS
  -> hourly_hot_collector.py
  -> SQLite news_items
  -> hot_topic_pipeline.py
  -> hot_clusters JSON
  -> cluster_context_builder
  -> basic_analysis_agent
  -> retriever
  -> expert_agent
  -> llm_expert_writer
```

知识库数据流是：

```text
data/knowledge/sources/**/*.txt
  -> scripts/run_knowledge_ingest.py
  -> data/knowledge/processed/documents.jsonl
  -> data/knowledge/processed/chunks.jsonl
  -> scripts/run_retriever.py
```

知识演化数据流是：

```text
documents.jsonl + chunks.jsonl
  -> scripts/run_knowledge_evolution.py
  -> data/knowledge/evolution/viewpoints.jsonl
  -> data/knowledge/evolution/view_evolution.jsonl
```

交互式 Agent 数据流是：

```text
用户问题
  -> scripts/run_agents.py
  -> task_planner
  -> 可选前置流水线调度器
  -> agent_orchestrator
  -> data/agent/responses 或 .agent_runtime/responses 下的响应 JSON
```

第一版 Agent 只支持三个 task type：

- `hot_news_query`
- `source_summary_request`
- `expert_topic_analysis`

## 重要入口

根目录兼容入口：

- `hourly_hot_collector.py`：运行 NewsNow + RSS 采集。
- `hot_topic_pipeline.py`：从 SQLite 中读取新闻并做热点聚类。
- `cluster_context_builder.py`：上下文构建器的根目录兼容入口。
- `db.py`：`app/storage/db.py` 的根目录兼容入口。

根目录 wrapper 规则：

- 不要在根目录 wrapper 里新增业务逻辑。
- 新代码应放到 `app/` 下。
- 新的可运行入口优先放到 `scripts/`。
- 保持 wrapper 足够薄并继续兼容，因为 Docker 和旧命令可能仍在使用它们。

推荐使用的脚本入口：

- `scripts/run_collector.py`
- `scripts/run_hot_pipeline.py`
- `scripts/run_context_builder.py`
- `scripts/run_basic_agent.py`
- `scripts/run_retriever.py`
- `scripts/run_expert_agent.py`
- `scripts/run_llm_expert_writer.py`
- `scripts/run_knowledge_ingest.py`
- `scripts/run_knowledge_evolution.py`
- `scripts/run_agents.py`

## 模块职责

`app/collectors/`

- `collector_common.py`：共享配置、路径、时间/文本工具、运行状态辅助函数。
- `newsnow_collector.py`：NewsNow 抓取、Markdown/raw/SQLite 标准化。
- `rss_collector.py`：RSS 源加载、增量过滤、Markdown/raw/SQLite 标准化。

采集调度规则：

- 默认 `RUN_MINUTE=58`。
- 默认 `RUN_IMMEDIATELY=false`。
- 13:58 运行时写入 `*_YYYY-MM-DD_13.*`。
- 13:58 到 14:00 之间发布的 RSS item 有意放到下一轮 14:58 采集，并写入 `*_14.*`。
- RSS 采集语义是增量采集。`rss_collector.py` 使用上一轮 `success` 或 `partial` 采集记录的 `finished_at` 作为下一轮窗口起点；没有成功历史记录时才回退到前 1 小时。除非用户明确要求改变 RSS 采集语义，否则不要改成全量抓取。
- 不要默认重新打开启动即采集；同一小时内立即采集会覆盖同名 Markdown/raw 文件。

`app/storage/`

- `db.py`：SQLite 表结构和插入/查询辅助函数。
- `sqlite_reader.py`、`file_store.py`：目前是轻量支持模块或占位模块。

`app/pipelines/`

- `hot_topic_pipeline.py`：SQLite -> 去重 -> 质量过滤 -> embedding -> 聚类 -> hot clusters。
- `cluster_context_builder.py`：hot clusters -> article ids -> SQLite 回查 -> cluster context。
- `dedup.py`、`clustering.py`、`quality_filters.py`：拆分目标或辅助模块。扩展前先看现有调用方式。

`app/agents/`

- `agent_orchestrator.py`：轻量对话编排器。每次查询都会自动运行所需的前置 pipeline 脚本，再按 task type 分发请求。
- `task_planner.py`：规则版任务规划器，当前只识别 `hot_news_query`、`source_summary_request`、`expert_topic_analysis`。
- `article_reader.py`：兼容转发层。不要在这里继续新增正文提取逻辑。
- `memory_store.py`：轻量文件记忆，保存上一轮热点列表和最近交互。
- `reflection_checker.py`：回答自检工具，用于提示原文读取失败、摘要兜底、缺 URL、缺发布时间等问题。
- `source_summary_report.py`：`source_summary_request` 的非专家 LLM/HTML 报告生成器，要求事实结论引用回原文摘录。
- `basic_analysis_agent.py`：基于规则，从 cluster context 生成基础分析。
- `expert_agent.py`：基于 context + basic analysis + retrieved context 生成规则增强版专家报告。
- `llm_expert_writer.py`：最终表达层。配置了 OpenAI-compatible chat completions 时调用 LLM，否则 fallback。
- `geopolitics_agent.py`、`markets_agent.py`、`tech_agent.py`、`synthesis_agent.py`：未来多专家 Agent 占位。

`app/rag/`

- `knowledge_ingest.py`：将 txt 知识卡片入库为 documents/chunks JSONL。
- `retriever.py`：基于 basic analysis，在 chunks 中做关键词检索。
- `knowledge_evolution.py`：离线观点抽取和观点演化记录。
- `knowledge_store.py`：知识库路径常量。
- `index_builder.py`：未来索引构建占位。

`app/tools/`

- `article_reader/`：独立 URL 正文读取工具，供 Agent 来源整理流程调用。
- `article_reader/reader.py`：主实现。优先直接请求网页并用 trafilatura 提取正文，然后 newspaper4k、Jina AI Reader、本地 HTML parser，最后由调用方回退 SQLite/RSS 摘要。
- `article_reader/schemas.py`：轻量输出结构类型定义。
- `article_reader/source_policies.py`：RSS 源正文读取策略加载器，支持 `enabled`、`rss_content`、`disabled`。

未来优化正文提取质量时，应只改 `app/tools/article_reader/`。主 Agent 只调用稳定接口：

```python
from app.tools.article_reader import read_article
```

`config/`

- `rss_sources.txt`：RSS 源列表。
- `rss_source_policies.txt`：RSS 源级正文读取策略。被 401/403/paywall 挡住但仍有新闻发现价值的源，应保留在 `rss_sources.txt`，并在这里标记为 `disabled`。
- `newsnow_frequency_words.txt`：NewsNow 质量过滤词。
- `newsnow_event_rules.txt`：NewsNow 事件打分规则。
- `basic_analysis_event_rules.txt`：事件分类和可靠来源规则。
- `collector.example.env`、`pipeline.example.env`：只是示例 env 文件，不会自动作为真实运行配置加载。

## 运行数据

重要输出位置：

- `data/db/data_hub.db`
- `data/markdown/newsnow/`
- `data/markdown/rss/`
- `data/raw/newsnow/`
- `data/raw/rss/`
- `data/hot/newsnow/`
- `data/hot/rss/`
- `data/analysis/context/`
- `data/analysis/reports/`
- `data/analysis/retrieved_context/`
- `data/analysis/expert_reports/`
- `data/analysis/llm_reports/`
- `data/knowledge/processed/`
- `data/knowledge/evolution/`
- `data/agent/session_state.json`
- `data/agent/responses/`
- `data/agent/article_cache/`
- `.agent_runtime/`：当 `data/agent/` 不可写时的 fallback 目录
- `logs/failed_sources.log`

运行生成物通常不应提交到 git。例外是人工整理过的知识源 txt，即 `data/knowledge/sources/` 下的内容。

## 交互式 Agent 注意事项

`scripts/run_agents.py` 是当前面向用户的 Agent 入口。

支持示例：

```bash
python scripts/run_agents.py --query "告诉我过去24小时的10条热点新闻"
python scripts/run_agents.py --query "请对1，2做内容整理和总结"
python scripts/run_agents.py --query "从专家的角度，分析过去一周关于以色列和伊朗战争的相关新闻"
```

常用参数：

- `--no-auto-pipeline`：不自动运行前置脚本。
- `--force-pipeline`：为兼容旧命令保留；默认自动流水线已经会在每次查询时重新生成前置产物。
- `--skip-llm`：自动流水线中跳过 LLM writer。
- `--json`：输出完整结构化响应。
- 常用命令示例：

```bash
python scripts/run_agents.py --query "告诉我过去24小时的10条热点新闻"
python scripts/run_agents.py --query "告诉我过去24小时的10条热点财经新闻"
python scripts/run_agents.py --query "告诉我过去24小时的10条热点国际新闻"
python scripts/run_agents.py --query "告诉我过去24小时的10条热点科技新闻"
python scripts/run_agents.py --query "告诉我过去24小时的10条热点新闻" --domain finance
python scripts/run_agents.py --query "告诉我过去24小时的10条热点新闻" --domain geopolitics
python scripts/run_agents.py --query "告诉我过去24小时的10条热点新闻" --domain tech_ai
python scripts/run_agents.py --query "请对1，2做内容整理和总结"
python scripts/run_agents.py --query "从专家的角度，分析过去一周关于以色列和伊朗战争的相关新闻"
python scripts/run_agents.py --query "告诉我过去24小时的10条热点新闻" --no-auto-pipeline
python scripts/run_agents.py --query "告诉我过去24小时的10条热点新闻" --skip-llm
```

前置流水线调度：

- `source_summary_request` 依赖上一轮热点列表的 session state，不自动运行完整 pipeline。
- 专家能力必须保持显式触发。planner 只有在用户 query 明确包含“专家”二字时，才应推断为 `expert_topic_analysis`；“分析”“预测”“影响”“判断”“怎么看”等泛化词如果没有“专家”，必须继续走普通新闻路径，除非命令显式传入 `--task-type expert_topic_analysis`。
- 热点查询支持三个返回阶段领域过滤：`finance`（财经）、`geopolitics`（地缘政治）、`tech_ai`（AI/科技）。planner 的触发词必须保持收敛：`财经` -> `finance`；`政治`、`国际`、`地缘`、`地缘政治` -> `geopolitics`；`科技`、`AI`、`ai` -> `tech_ai`。更丰富的领域关键词可以只用于返回阶段的 cluster 打分。领域过滤必须发生在全量热点生成之后；不要为了普通领域请求过滤采集或聚类输入。
- `hot_news_query` 默认每次查询都重新生成 hot topic、context、basic analysis、`run_llm_expert_writer --mode news`，除非显式使用 `--no-auto-pipeline`。普通热点查询必须保持普通新闻事实整理，不加入专家推断。
- `expert_topic_analysis` 默认会重新生成 hot topic、context、basic analysis、knowledge ingest、retriever、expert report、`run_llm_expert_writer --mode expert`，然后在本轮生成的 expert / LLM report 中做关键词匹配，除非显式使用 `--no-auto-pipeline`。
- 用户请求的时间窗口会传入 `run_hot_pipeline.py`。例如 `告诉我过去24小时的10条热点新闻` 会变成 `python scripts/run_hot_pipeline.py --window-hours 24`。
- `agent_orchestrator.py` 必须把本轮 hot cluster 文件显式传给 `run_context_builder.py --input-file ...`；不要让 context builder 通过 glob 排序猜 hot 文件，避免 3 小时请求混入旧的 5h/24h 热点结果。
- hot topic 时间窗口采用最近 N 个完整小时，而不是滚动到当前分钟。例如当前时间 18:42，用户要求过去 3 小时，应分析 `[15:00, 18:00)`，也就是 15、16、17 三个完整小时。
- hot cluster 输出会记录 `analysis_window_hours`、`analysis_window` 和 `data_coverage`。如果数据库不满足完整请求窗口，最终 Agent 回答需要提醒用户，例如：“当前数据库没有完整的最近 24 个完整小时数据，现有数据库约覆盖过去 5 小时，下面仅根据这部分数据给出分析结果。”
- `cluster_context_builder.py` 会校验 hot 文件的 `source_type`。如果 SQLite 中同一个 article id 已经指向不同来源或不同标题，会优先使用 hot cluster 内嵌的文章快照，并记录/打印 warning，不能静默挂上不相干文章。
- token 成本：只有 `run_llm_expert_writer` 会在配置了 `LLM_EXPERT_WRITER_API_KEY`、`LLM_EXPERT_WRITER_BASE_URL`、`LLM_EXPERT_WRITER_MODEL` 时调用 OpenAI-compatible chat completions 并消耗 LLM token。`--mode news` 是普通新闻事实整理，只基于来源标题、摘要和上下文做客观归纳；`--mode expert` 才是专家分析表达层。`hot` 使用本地 sentence-transformers embedding 和 sklearn 聚类；context/basic/knowledge/retrieved/expert 都是本地处理。

来源整理链路：

```text
RSS 源策略
  -> enabled：
       文章 URL
         -> trafilatura
         -> newspaper4k
         -> Jina AI Reader: https://r.jina.ai/{文章 URL}
         -> 本地 HTML parser
         -> SQLite/RSS summary 或 title 兜底
  -> rss_content：
       不访问文章 URL，直接使用 RSS item 的 content:encoded
  -> disabled：
       跳过正文抓取，直接使用 RSS/SQLite 摘要或标题
```

注意：

- 现在优先使用 `trafilatura`。对当前 RSS 源抽样测试后，它整体比 Jina Reader 更适合正文分析；Jina Reader 保留为兜底层。
- `trafilatura` 和 `newspaper4k` 是可选增强层。它们已写入 requirements，但如果当前环境没有安装，代码会安全跳过。
- `article_reader.py` 只缓存成功读取的结果。网络失败、403、策略禁用等结果不缓存为全文。
- `EXTRACTOR_VERSION` 控制缓存版本；修改正文提取语义时要 bump 版本。
- `content_fetch_status` 用来区分全文和摘要兜底。Bloomberg / NYT / MarketWatch / WSJ / Economist 这类被 401/403/paywall 或授权限制挡住的页面，应继续作为 RSS 新闻信号保留，但不要伪装成全文。
- Fox News World 当前使用 `rss_content`，因为它的 RSS item 在 `content:encoded` 里提供可用正文。
- 对 `请对1做内容整理` 这类 `source_summary_request`，Agent 应读取所选热点 cluster 里的全部文章链接。不要限制最多 5 个来源，也不要按媒体/source name 去重；同一媒体在同一 cluster 中出现多篇文章时全部保留。进入内容分析前，要过滤 `config/rss_source_policies.txt` 中 `article_reading=disabled` 的 RSS 来源。这些来源只用于热点发现和热度判断，不进入正文分析主体。回答中必须透明提示，例如：`内容分析已排除 34 条无法读取正文的来源：Bloomberg Markets 18 条，NYT World 16 条；这些来源仅用于热点发现和热度判断。`
- 原文读取后，`source_summary_request` 应输出一份非专家来源报告到 `data/agent/source_reports/source_summary_*.html` 和 JSON。报告按媒体源分组，总结各媒体报道重点，并整理共同事实、差异与补充、信息缺口。LLM 生成必须使用低温，temperature 上限为 `0.15`；每个事实判断都必须带 citation id，指回已读取原文摘录。如果 LLM 未配置、调用失败或引用校验不通过，必须使用 fallback，而不是输出无引用的大模型发挥。
- `source_summary_request` 必须保持客观来源整理口径：不加入专家判断，不做主观发挥。
- `reflection_checker.py` 会在原文读取失败或摘要兜底时追加自检提示。

建议新对话中使用这个提示来专门开发该模块：

```text
优化 article_reader 工具。
请先阅读 AGENT.md 和 app/tools/article_reader/，
只围绕正文提取模块开发，不要改主 Agent 流程。
```

## LLM Writer 注意事项

`app/agents/llm_expert_writer.py` 会用 `python-dotenv` 加载根目录 `.env`。

需要的环境变量：

```env
LLM_EXPERT_WRITER_API_KEY=
LLM_EXPERT_WRITER_BASE_URL=
LLM_EXPERT_WRITER_MODEL=
LLM_EXPERT_WRITER_TIMEOUT=90
LLM_EXPERT_WRITER_TEMPERATURE=0.4
```

它使用 OpenAI-compatible `/chat/completions` 协议。

重要护栏：

- `llm_expert_writer.py` 有两个模式。`--mode news` 读取 cluster context + basic analysis，生成 `final_summary`、`source_grounded_summary`、`known_facts`、`uncertainties` 等普通新闻事实整理字段，不应填充专家解释；`--mode expert` 读取 `expert_report`，才允许生成专家分析字段。
- 结构字段必须永远从上游 source item 继承。
- expert 模式下，LLM 只能生成叙事字段，例如 `final_summary`、`expert_analysis`、`why_it_really_matters`、`key_risk`、`uncertainty`、`watch_points`、`podcast_hook`。
- 如果 API 失败、返回空内容、返回坏 JSON 或触发 guardrail，则使用 fallback 输出。

不要随意放松这些护栏。用户很重视可追踪性和 schema 稳定性。

## 知识库边界

主项目不负责从 YouTube 或网页自动生成知识卡片。这条支线之前已经从主仓库移除。

保持这个边界：

```text
外部工具可以生成 txt 知识卡片。
本项目只读取 data/knowledge/sources/**/*.txt，
然后执行入库和检索。
```

除非用户明确推翻这个架构决策，不要把 YouTube 字幕下载、Whisper、NotebookLM、频道监控或 LLM 自动摘要生成重新加回本仓库。

## 常用命令

运行采集：

```bash
python scripts/run_collector.py
```

运行热点发现：

```bash
python scripts/run_hot_pipeline.py
```

构建 cluster context：

```bash
python scripts/run_context_builder.py
```

运行规则基础分析：

```bash
python scripts/run_basic_agent.py
```

知识入库：

```bash
python scripts/run_knowledge_ingest.py
```

知识检索：

```bash
python scripts/run_retriever.py
```

生成专家报告：

```bash
python scripts/run_expert_agent.py
```

运行 LLM writer：

```bash
python scripts/run_llm_expert_writer.py
```

生成知识演化层：

```bash
python scripts/run_knowledge_evolution.py
```

运行交互式 Agent：

```bash
python scripts/run_agents.py --query "告诉我过去24小时的10条热点新闻"
```

## 验证方式

小改动至少运行定向编译检查：

```bash
python -m py_compile path/to/changed_file.py
```

pipeline 相关改动优先直接运行对应脚本。很多脚本在数据缺失时会优雅降级。

较完整的检查命令：

```bash
python -m py_compile app/agents/basic_analysis_agent.py app/agents/expert_agent.py app/agents/llm_expert_writer.py app/pipelines/hot_topic_pipeline.py app/pipelines/cluster_context_builder.py app/rag/knowledge_ingest.py app/rag/retriever.py app/rag/knowledge_evolution.py
```

Windows 下如果 `__pycache__` 写入权限失败，可以使用：

```bash
$env:PYTHONDONTWRITEBYTECODE='1'; python -m py_compile path/to/file.py
```

## 开发规则

- 保持改动小而集中。
- 除非用户明确要求，不要改 SQLite 表结构。
- 做 agents/RAG 时不要顺手改 collector 行为。
- 不要在同一次改动里同时大改 LLM prompt 和 retriever 规则，除非任务明确要求。
- 保持根目录兼容入口可用。
- 除非用户明确要求，不要改输出 schema。
- 不要提交生成的数据库、日志或大型运行产物。
- 不要提交 `data/agent/article_cache/`、`data/agent/responses/*.json` 或 `.agent_runtime/`。
- 手工编辑文件时使用 `apply_patch`。

## 已知注意点

- 部分历史知识 txt 可能存在编码问题。除非任务明确是数据清洗，否则不要静默重写它们。
- `config/*.example.env` 是示例文件。真实运行值应放在 `.env` 或真实环境变量里。
- LLM report 中的 `generation_mode` 目前只有当本轮所有 item 都成功走 LLM 时才是 `llm`；混合 fallback 时会记为 `fallback`。
- 项目仍在从根目录脚本逐步迁移到 `app/` 模块。除非用户要求架构整理，不要做大规模搬迁。
