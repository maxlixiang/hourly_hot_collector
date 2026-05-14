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

## Important Entry Points

Root compatibility wrappers:

- `hourly_hot_collector.py`: runs NewsNow + RSS collection.
- `hot_topic_pipeline.py`: runs hot topic clustering from SQLite.
- `cluster_context_builder.py`: root wrapper for context builder.
- `db.py`: root wrapper for `app/storage/db.py`.

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

## Module Responsibilities

`app/collectors/`

- `collector_common.py`: shared config, paths, time/text helpers, run status helpers.
- `newsnow_collector.py`: NewsNow source fetching, markdown/raw/SQLite standardization.
- `rss_collector.py`: RSS source loading, incremental filtering, markdown/raw/SQLite standardization.

`app/storage/`

- `db.py`: SQLite schema and insert/query helpers.
- `sqlite_reader.py`, `file_store.py`: currently light/placeholder support modules.

`app/pipelines/`

- `hot_topic_pipeline.py`: SQLite -> dedup -> quality filter -> embeddings -> clustering -> hot clusters.
- `cluster_context_builder.py`: hot clusters -> article ids -> SQLite lookup -> cluster context.
- `dedup.py`, `clustering.py`, `quality_filters.py`: extraction targets or helper modules. Follow existing usage before expanding them.

`app/agents/`

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

`config/`

- `rss_sources.txt`: RSS source list.
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
- `logs/failed_sources.log`

Generated outputs are usually ignored by git, except curated knowledge source txt files under `data/knowledge/sources/`.

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

- Structural fields must always inherit from the original `expert_report`.
- The LLM may only write narrative fields such as `final_summary`, `expert_analysis`, `why_it_really_matters`, `key_risk`, `uncertainty`, `watch_points`, and `podcast_hook`.
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

## 重要入口

根目录兼容入口：

- `hourly_hot_collector.py`：运行 NewsNow + RSS 采集。
- `hot_topic_pipeline.py`：从 SQLite 中读取新闻并做热点聚类。
- `cluster_context_builder.py`：上下文构建器的根目录兼容入口。
- `db.py`：`app/storage/db.py` 的根目录兼容入口。

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

## 模块职责

`app/collectors/`

- `collector_common.py`：共享配置、路径、时间/文本工具、运行状态辅助函数。
- `newsnow_collector.py`：NewsNow 抓取、Markdown/raw/SQLite 标准化。
- `rss_collector.py`：RSS 源加载、增量过滤、Markdown/raw/SQLite 标准化。

`app/storage/`

- `db.py`：SQLite 表结构和插入/查询辅助函数。
- `sqlite_reader.py`、`file_store.py`：目前是轻量支持模块或占位模块。

`app/pipelines/`

- `hot_topic_pipeline.py`：SQLite -> 去重 -> 质量过滤 -> embedding -> 聚类 -> hot clusters。
- `cluster_context_builder.py`：hot clusters -> article ids -> SQLite 回查 -> cluster context。
- `dedup.py`、`clustering.py`、`quality_filters.py`：拆分目标或辅助模块。扩展前先看现有调用方式。

`app/agents/`

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

`config/`

- `rss_sources.txt`：RSS 源列表。
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
- `logs/failed_sources.log`

运行生成物通常不应提交到 git。例外是人工整理过的知识源 txt，即 `data/knowledge/sources/` 下的内容。

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

- 结构字段必须永远从原始 `expert_report` 继承。
- LLM 只能生成叙事字段，例如 `final_summary`、`expert_analysis`、`why_it_really_matters`、`key_risk`、`uncertainty`、`watch_points`、`podcast_hook`。
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
- 手工编辑文件时使用 `apply_patch`。

## 已知注意点

- 部分历史知识 txt 可能存在编码问题。除非任务明确是数据清洗，否则不要静默重写它们。
- `config/*.example.env` 是示例文件。真实运行值应放在 `.env` 或真实环境变量里。
- LLM report 中的 `generation_mode` 目前只有当本轮所有 item 都成功走 LLM 时才是 `llm`；混合 fallback 时会记为 `fallback`。
- 项目仍在从根目录脚本逐步迁移到 `app/` 模块。除非用户要求架构整理，不要做大规模搬迁。
