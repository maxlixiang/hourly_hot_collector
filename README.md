# hourly_hot_collector

`hourly_hot_collector` 是一个面向热点采集与热点发现的 Python 单仓库项目，当前已经具备：
- NewsNow + RSS 双采集
- Markdown 快照输出
- Raw JSON 落盘
- SQLite 结构化存储
- 热点聚类与热度排序
- 为后续 Agent / RAG / 专家分析层预留架构空间

## 项目当前能力

### 1. 数据采集层
- 按小时抓取 NewsNow 热榜快照
- 抓取 RSS 增量新闻
- 将结果写入 Markdown、Raw JSON、SQLite
- 记录失败源日志

### 2. 数据存储层
- 使用 SQLite 作为轻量数据库
- 存储 `fetch_runs` 与 `news_items`
- 为后续去重、事件跟踪、Agent 分析打基础

### 3. 热点发现层
- 直接从 SQLite 读取数据
- 进行时间窗口过滤
- 进行轻量去重
- 分别对 `newsnow` 和 `rss` 做聚类
- 输出热点簇 JSON

### 4. NewsNow 质量控制
- 外部 `frequency_words` 规则过滤
- 外部 `news_event_score` 规则过滤
- 更适合从平台热榜中提取“更像新闻事件”的标题

## 当前目录结构

```text
hourly_hot_collector/
├─ app/
├─ config/
├─ data/
├─ docs/
├─ logs/
├─ scripts/
├─ tests/
├─ hourly_hot_collector.py
├─ hot_topic_pipeline.py
├─ db.py
└─ requirements.txt
```

关键目录说明：
- `app/`：正在逐步沉淀为正式模块结构
- `config/`：运行配置、规则文件、示例 env
- `data/db/`：SQLite 数据库
- `data/raw/`：原始 JSON 数据
- `data/markdown/`：Markdown 输出
- `data/hot/`：热点发现输出
- `logs/`：运行日志与失败日志
- `docs/`：架构、数据流、输入输出与发布规则文档

## 配置文件

主要配置来自项目根目录的 `.env`。

当前常用配置文件包括：
- `config/rss_sources.txt`
- `config/newsnow_frequency_words.txt`
- `config/newsnow_event_rules.txt`
- `config/collector.example.env`
- `config/pipeline.example.env`

## 安装依赖

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## 运行采集器

```bash
python hourly_hot_collector.py
```

采集器运行后会写入：
- `data/markdown/newsnow/`
- `data/markdown/rss/`
- `data/raw/newsnow/`
- `data/raw/rss/`
- `data/db/data_hub.db`
- `logs/failed_sources.log`

## 运行热点发现 Pipeline

```bash
python hot_topic_pipeline.py
```

运行后会输出到：
- `data/hot/newsnow/`
- `data/hot/rss/`

## 也可以使用脚本入口

```bash
python scripts/run_collector.py
python scripts/run_hot_pipeline.py
```

## 当前架构状态

为了兼容现有运行方式，项目目前保留了根目录入口：
- `hourly_hot_collector.py`
- `hot_topic_pipeline.py`
- `db.py`

同时核心逻辑正在逐步迁移到：
- `app/collectors/`
- `app/pipelines/`
- `app/storage/`

未来预留的层包括：
- `app/agents/`
- `app/rag/`
- `app/schemas/`
- `app/utils/`

## 相关文档

- [架构说明](docs/ARCHITECTURE.md)
- [数据流说明](docs/DATA_FLOW.md)
- [Agent 规划](docs/AGENTS.md)
- [输入输出规范](docs/IO_SPEC.md)
- [Release / Tag 规则](docs/RELEASE.md)

## Release / Tag 规则

当前采用轻量语义化版本规则：
- `v0.1.0`
- `v0.2.0`
- `v1.0.0`

版本含义：
- `PATCH`：小修复、小调整
- `MINOR`：新功能、非破坏升级
- `MAJOR`：破坏性变更或运行方式调整

详细规则见：
[docs/RELEASE.md](docs/RELEASE.md)

## 项目定位

这个项目当前不是一个“大而全”的新闻平台，而是一个：

1. 可持续运行的热点采集系统  
2. 可持续演进的热点发现系统  
3. 为未来多专家 Agent / RAG 分析层准备的基础设施

当前阶段重点是：
- 保持采集稳定
- 保持热点发现质量
- 逐步整理工程结构
- 为后续分析层提供干净的数据接口
