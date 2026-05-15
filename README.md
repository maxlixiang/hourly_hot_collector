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

推荐入口位于 `scripts/`。根目录保留少量兼容 wrapper，方便旧命令和部署配置继续运行；新的业务逻辑不要继续写到根目录 wrapper 里。

推荐的新入口：

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

根目录兼容入口：

| 文件 | 作用 |
| --- | --- |
| `hourly_hot_collector.py` | 兼容采集入口。Docker / 旧命令可继续使用；新使用优先 `scripts/run_collector.py`。 |
| `hot_topic_pipeline.py` | 兼容热点发现入口；新使用优先 `scripts/run_hot_pipeline.py`。 |
| `cluster_context_builder.py` | 兼容上下文构建入口；新使用优先 `scripts/run_context_builder.py`。 |
| `db.py` | SQLite 存储层兼容入口，真实实现位于 `app/storage/db.py`。 |
| `main.py` | 简单主入口，目前调用采集器。 |

## 项目结构

```text
hourly_hot_collector/
├─ app/
│  ├─ collectors/        # NewsNow / RSS 采集逻辑
│  ├─ pipelines/         # 热点发现、上下文构建
│  ├─ agents/            # 基础分析、专家报告、LLM 成稿
│  ├─ tools/             # 可复用工具，例如 article_reader
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

采集器默认按小时守护运行，并在每小时第 58 分钟执行一次采集：

```text
RUN_MINUTE=58
RUN_IMMEDIATELY=false
```

例如 13:58 运行的结果会写入：

```text
data/markdown/rss/rss_hot_YYYY-MM-DD_13.md
data/markdown/newsnow/daily_hot_YYYY-MM-DD_13.md
```

13:58 到 14:00 之间新发布的 RSS 文章会进入下一轮 14:58 的采集窗口，并写入 `*_14.*` 文件。默认关闭启动即采集，是为了避免在同一小时内多次启动程序时覆盖同名 Markdown/raw 文件。

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

`source_summary_request` 现在已经有一套相对完整的正文读取链路。它会先根据 RSS 源策略判断是否应该抓正文，然后再进入正文提取工具：

```text
RSS 源策略
  -> enabled: 访问文章 URL 抓正文
       -> trafilatura
       -> newspaper4k
       -> Jina Reader: https://r.jina.ai/{文章URL}
       -> 本地 HTML parser
       -> SQLite/RSS summary 或 title 兜底
  -> rss_content: 不访问文章 URL，直接使用 RSS item 的 content:encoded
  -> disabled: 不抓正文，只保留 RSS 标题/摘要用于新闻发现
```

这套顺序来自对多组 RSS 源的抽样测试：`trafilatura` 对多数新闻站、科技站和财经站的正文质量明显优于 Jina Reader，因此现在作为第一优先级；`newspaper4k` 和 Jina Reader 是增强/兜底层；本地 HTML parser 是最后的无外部依赖兜底。

RSS 源策略保存在：

- `config/rss_source_policies.txt`

当前策略值：

```text
enabled      默认，允许抓网页正文
rss_content  不访问文章 URL，直接使用 RSS content:encoded，例如 Fox News World
disabled     不抓正文，只保留 RSS 标题/摘要，例如 NYT、Bloomberg、MarketWatch、WSJ、Economist
```

这些 `disabled` 源仍然保留在 `config/rss_sources.txt` 中，用于热点发现和“发生了什么”的信号；只有在需要全文整理时才会跳过正文抓取，避免 401/403、超时和低质量正文污染结果。

如果目标网站 403、网络不可用或正文提取失败，Agent 不会中断，会回退到本地数据库摘要，并在回答末尾输出“自检提示”。

正文读取结果会带有内容状态：

- `full_text`：已读取到较完整正文。
- `partial_text`：读取到部分正文。
- `summary_only`：目标源可能 401/403/paywall，只能使用摘要级信息。
- `summary_fallback`：正文读取失败，已回退 SQLite/RSS 摘要或标题。

成功读取的原文缓存保存在：

- `data/agent/article_cache/`

该目录属于运行缓存，已被 git 忽略。

正文读取已经被拆成仓库内独立工具层：

```text
app/tools/article_reader/
├─ __init__.py
├─ reader.py           # 主实现：trafilatura / newspaper4k / Jina / 本地 HTML fallback
├─ schemas.py          # 输出结构类型定义
└─ source_policies.py  # RSS 源正文读取策略
```

主 Agent 只调用稳定接口：

```python
from app.tools.article_reader import read_article
```

后续如果要专门优化正文提取质量，可以开一个新对话，聚焦：

```text
优化 article_reader 工具。
请只阅读 README.md、AGENT.md 和 app/tools/article_reader/，
不要修改主 Agent 流程。
```

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

## news_search 工具模块

`app/tools/news_search/` 是独立的本地 SQLite 新闻检索工具，用于从 `data/db/data_hub.db` 的 `news_items` 表中按主题、时间窗口和 `source_type` 检索相关新闻。它目前服务于 Agent 的 `expert_topic_analysis` SQLite fallback，也可以被其他本地脚本复用。

稳定接口：

```python
from app.tools.news_search import search_news

items = search_news(
    base_dir=PROJECT_ROOT,
    query="以色列 伊朗 战争",
    window_hours=24,
    source_type="mixed",
    limit=20,
)
```

`source_type` 支持 `newsnow`、`rss`、`mixed`。排序规则是标题命中优先，其次摘要命中，再按发布时间/抓取时间从新到旧。

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
