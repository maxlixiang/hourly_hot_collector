# hourly_hot_collector

`hourly_hot_collector` 是一个本地运行的新闻热点采集、热点发现和分析增强项目。

它当前已经支持：

- 抓取 NewsNow 热榜数据
- 抓取 RSS 新闻源
- 将新闻写入 SQLite
- 输出 Markdown、raw JSON 和结构化数据库
- 从已抓取新闻中发现热点簇
- 构建热点上下文
- 做基础规则分析、专家知识检索、专家报告和 LLM 成稿
- 管理本地专家知识库 txt，并生成观点演化文件

这个项目不是一个 Web 服务，也不是一个大平台。它更像一个持续演进的本地新闻分析工作台：先稳定采集，再结构化存储，再逐步叠加热点发现、RAG、专家分析和最终表达层。

## 主要入口

根目录保留了几个兼容入口，方便直接运行：

| 文件 | 作用 |
| --- | --- |
| `hourly_hot_collector.py` | 抓新闻。运行 NewsNow + RSS 采集，写 Markdown、raw JSON、SQLite。 |
| `hot_topic_pipeline.py` | 从已抓取的新闻里找热点。读取 SQLite，去重、聚类、排序，输出热点簇。 |
| `cluster_context_builder.py` | 根据热点簇回查 SQLite，构建给 Agent 使用的上下文包。 |
| `db.py` | SQLite 存储层兼容入口，真实实现位于 `app/storage/db.py`。 |
| `main.py` | 简单主入口，目前调用采集器。 |

推荐的新入口位于 `scripts/`：

| 脚本 | 作用 |
| --- | --- |
| `scripts/run_collector.py` | 运行新闻采集器。 |
| `scripts/run_hot_pipeline.py` | 运行热点发现 pipeline。 |
| `scripts/run_context_builder.py` | 将热点簇转成 cluster context。 |
| `scripts/run_basic_agent.py` | 基于规则生成基础分析报告。 |
| `scripts/run_retriever.py` | 从知识库 chunks 中检索相关专家知识片段。 |
| `scripts/run_expert_agent.py` | 生成规则增强版专家报告。 |
| `scripts/run_llm_expert_writer.py` | 调用 LLM 或 fallback，生成更自然的最终分析稿。 |
| `scripts/run_knowledge_ingest.py` | 将 `data/knowledge/sources/**/*.txt` 入库为 documents/chunks JSONL。 |
| `scripts/run_knowledge_evolution.py` | 生成知识库观点层和观点演化层。 |
| `scripts/run_agents.py` | 新闻分析 Agent 对话入口。支持热点查询、来源整理、专家专题分析，并可自动调度前置流水线。 |

## 项目结构

```text
hourly_hot_collector/
├─ app/
│  ├─ collectors/        # NewsNow / RSS 采集逻辑
│  ├─ pipelines/         # 热点发现、上下文构建
│  ├─ agents/            # 基础分析、专家报告、LLM 成稿
│  ├─ rag/               # 知识入库、检索、观点演化
│  ├─ storage/           # SQLite 读写
│  ├─ schemas/           # 预留数据结构定义
│  └─ utils/             # 预留通用工具
├─ config/               # RSS 源、过滤词、规则、示例 env
├─ data/
│  ├─ db/                # SQLite 数据库
│  ├─ raw/               # 原始抓取数据
│  ├─ markdown/          # Markdown 快照
│  ├─ hot/               # 热点簇输出
│  ├─ analysis/          # context / reports / llm reports
│  └─ knowledge/         # 专家知识库 sources / processed / evolution
├─ docs/                 # 架构、数据流、知识卡片等文档
├─ logs/                 # 运行日志
├─ scripts/              # 推荐运行入口
├─ tests/                # 基础测试
├─ .env                  # 本地运行配置
├─ requirements.txt
├─ Dockerfile
└─ docker-compose.yml
```

## 安装

建议使用 Python 3.11 或更新版本。

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## 配置

主要配置在项目根目录 `.env` 中。

可以参考：

- `config/collector.example.env`
- `config/pipeline.example.env`

RSS 源配置：

- `config/rss_sources.txt`

NewsNow 质量规则：

- `config/newsnow_frequency_words.txt`
- `config/newsnow_event_rules.txt`

基础分析事件规则：

- `config/basic_analysis_event_rules.txt`

如果要启用 LLM 最终写作层，需要在 `.env` 中配置：

```env
LLM_EXPERT_WRITER_API_KEY=
LLM_EXPERT_WRITER_BASE_URL=
LLM_EXPERT_WRITER_MODEL=
LLM_EXPERT_WRITER_TIMEOUT=90
LLM_EXPERT_WRITER_TEMPERATURE=0.4
```

`LLM_EXPERT_WRITER_BASE_URL` 使用 OpenAI-compatible Chat Completions 协议，例如 DeepSeek 可配置为：

```env
LLM_EXPERT_WRITER_BASE_URL=https://api.deepseek.com
```

## 典型运行流程

### 1. 抓取新闻

```bash
python hourly_hot_collector.py
```

或：

```bash
python scripts/run_collector.py
```

输出包括：

- `data/markdown/newsnow/`
- `data/markdown/rss/`
- `data/raw/newsnow/`
- `data/raw/rss/`
- `data/db/data_hub.db`
- `logs/failed_sources.log`

### 2. 发现热点

```bash
python hot_topic_pipeline.py
```

或：

```bash
python scripts/run_hot_pipeline.py
```

输出：

- `data/hot/newsnow/newsnow_hot_clusters_*.json`
- `data/hot/rss/rss_hot_clusters_*.json`

### 3. 构建热点上下文

```bash
python scripts/run_context_builder.py
```

输出：

- `data/analysis/context/newsnow_cluster_context_*.json`
- `data/analysis/context/rss_cluster_context_*.json`

### 4. 生成基础分析

```bash
python scripts/run_basic_agent.py
```

输出：

- `data/analysis/reports/newsnow_basic_analysis_*.json`
- `data/analysis/reports/rss_basic_analysis_*.json`

### 5. 检索专家知识

先确保知识库已经入库：

```bash
python scripts/run_knowledge_ingest.py
```

然后运行检索：

```bash
python scripts/run_retriever.py
```

输出：

- `data/analysis/retrieved_context/newsnow_retrieved_context_*.json`
- `data/analysis/retrieved_context/rss_retrieved_context_*.json`

### 6. 生成专家报告

```bash
python scripts/run_expert_agent.py
```

输出：

- `data/analysis/expert_reports/newsnow_expert_report_*.json`
- `data/analysis/expert_reports/rss_expert_report_*.json`

### 7. 生成 LLM 成稿

```bash
python scripts/run_llm_expert_writer.py
```

如果 LLM 配置完整，会优先调用真实模型。如果配置缺失或调用失败，会自动 fallback，保证流程不中断。

输出：

- `data/analysis/llm_reports/newsnow_llm_report_*.json`
- `data/analysis/llm_reports/rss_llm_report_*.json`

### 8. 生成知识观点演化层

```bash
python scripts/run_knowledge_evolution.py
```

输出：

- `data/knowledge/evolution/viewpoints.jsonl`
- `data/knowledge/evolution/view_evolution.jsonl`

## 新闻分析 Agent v1

当前项目已经有一个轻量对话式 Agent 入口：

```bash
python scripts/run_agents.py --query "告诉我过去24小时的10条热点新闻"
```

第一版只支持 3 个 `task_type`：

| task_type | 作用 | 示例 |
| --- | --- | --- |
| `hot_news_query` | 查询最近热点新闻，基于本地 hot / report 文件返回热点列表。 | `告诉我过去24小时的10条热点新闻` |
| `source_summary_request` | 对上一轮热点列表中的指定编号做来源整理。 | `请对1，2做内容整理和总结` |
| `expert_topic_analysis` | 复用已有专家报告 / LLM 报告，对某个专题做专家视角分析。 | `从专家的角度，分析过去一周关于以色列和伊朗战争的相关新闻` |

### Agent 自动流水线

`run_agents.py` 默认会检查并复用前置产物。如果缺少必要文件，会自动运行相应脚本：

```text
hot_news_query
  -> run_hot_pipeline
  -> run_context_builder
  -> run_basic_agent
  -> run_knowledge_ingest
  -> run_retriever
  -> run_expert_agent
  -> run_llm_expert_writer
  -> run_agents
```

常用参数：

```bash
python scripts/run_agents.py --query "告诉我过去24小时的10条热点新闻"
python scripts/run_agents.py --query "请对1，2做内容整理和总结"
python scripts/run_agents.py --query "从专家的角度，分析过去一周关于以色列和伊朗战争的相关新闻"
python scripts/run_agents.py --query "告诉我过去24小时的10条热点新闻" --no-auto-pipeline
python scripts/run_agents.py --query "告诉我过去24小时的10条热点新闻" --force-pipeline
python scripts/run_agents.py --query "告诉我过去24小时的10条热点新闻" --skip-llm
```

### 内容整理与原文读取

`source_summary_request` 现在会优先尝试读取原文：

```text
文章 URL
  -> Jina AI Reader: https://r.jina.ai/{文章URL}
  -> 本地 HTML 正文提取兜底
  -> SQLite summary / title 兜底
```

Jina Reader 用于降低导航栏、广告、页脚等无效信息进入“主要内容”的概率。读取成功时会显示：

```text
原文读取：成功（Jina Reader）
```

如果目标网站 403、网络不可用或正文提取失败，Agent 不会中断，会回退到本地数据库摘要，并在回答末尾输出“自检提示”。

成功读取的原文缓存保存在：

- `data/agent/article_cache/`

该目录属于运行缓存，已被 git 忽略。

### Agent 记忆与自检

当前 memory 是轻量文件记忆：

- 记住上一轮热点列表，供 `请对1，2做内容整理和总结` 继续引用。
- 记录最近交互历史。
- 优先写入 `data/agent/session_state.json`，如权限失败则回退到 `.agent_runtime/session_state.json`。

Reflection v1 会检查：

- 是否成功读取原文
- 是否使用了数据库摘要兜底
- 是否缺少 URL
- 是否缺少可解析发布时间

这一层目前只做安全提示，不会自动改写最终结论。

## 知识库

本项目只负责读取本地 txt 专家知识，不负责自动从视频或网页生成知识卡片。

知识源目录：

```text
data/knowledge/sources/
├─ geopolitics/
├─ markets/
├─ tech/
└─ general/
```

推荐一个主题一个 txt 文件，不要把多个主题混进一个大文件。写法参考：

- `docs/KNOWLEDGE_CARD_GUIDE.md`

入库后会生成：

- `data/knowledge/processed/documents.jsonl`
- `data/knowledge/processed/chunks.jsonl`

## Docker

```bash
docker-compose up --build
```

Docker 会挂载：

- `data/`
- `config/`
- `logs/`

## 文档

- [架构说明](docs/ARCHITECTURE.md)
- [数据流说明](docs/DATA_FLOW.md)
- [Agent 规划](docs/AGENTS.md)
- [输入输出规范](docs/IO_SPEC.md)
- [知识卡片指南](docs/KNOWLEDGE_CARD_GUIDE.md)
- [发布规则](docs/RELEASE.md)

## 开发状态

当前项目仍处在快速迭代阶段。根目录入口会继续保留，方便运行；核心实现会逐步沉淀到 `app/` 下。

当前主线是：

```text
采集新闻
  -> SQLite 入库
  -> 热点发现
  -> 上下文构建
  -> 基础分析
  -> 专家知识检索
  -> 专家报告
  -> LLM 成稿
```
