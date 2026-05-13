# 知识卡片指南

本项目的知识库不是逐字稿仓库，而是给 `knowledge_ingest -> chunks.jsonl -> retriever` 使用的专题资料层。

目标很明确：

- 让知识内容更适合被关键词检索命中
- 让每个 chunk 尽量围绕单一主题
- 让后续 Agent 读取到的是“可复用观点和框架”，而不是大段杂谈

## 为什么每个专题一个 txt 文件

RAG 检索在 v1 阶段还是轻量关键词检索，不是向量检索。

如果把一周多个主题、多个国家、多个市场观点混在一个大文件里，会带来三个问题：

- 一个 chunk 同时包含很多主题，容易被泛词误命中
- 高相关专题会被大杂烩文档稀释
- 后续 Agent 很难判断某段知识究竟属于哪个问题域

所以当前强烈建议：

- 每个视频一个 txt
- 每篇文章一个 txt
- 每个专题一个 txt

不要把多个无关主题混在一个大文件里。

## 推荐文件命名规则

建议使用稳定、可读、便于检索的英文 snake_case：

```text
iran_hormuz_energy_risk.txt
google_nvidia_ai_chips.txt
us_equity_risk_sentiment.txt
```

建议命名包含：

- 核心国家 / 公司 / 资产
- 核心议题
- 核心风险或主题

避免使用：

- `notes1.txt`
- `video_summary.txt`
- `weekly_mix.txt`

## 推荐正文结构

建议统一使用下面这种知识卡片结构：

```text
# title:
# expert:
# date:
# source_type:
# original_url:
# domain:
# tags:

## 本期核心事实

## 主要观点

## 判断框架

## 可能影响

## 后续观察点

## 适用检索关键词
```

这种结构有几个好处：

- 标题和 metadata 方便定位专题
- 分段标题天然适合 chunk 切分
- “判断框架 / 观察点 / 检索关键词” 对后续 Agent 特别有用

## 哪些内容适合放进知识库

优先放这些：

- 主要事实
- 专家主要判断
- 专家的分析框架
- 影响路径
- 后续观察点
- 适用检索关键词

适合的内容风格：

- 结构清晰
- 一段只讲一个点
- 语言尽量明确
- 有主题聚焦

## 哪些内容不适合

不建议放这些：

- 大量寒暄和开场白
- 口语化重复表达
- 跑题的闲聊
- 多个不相关主题混在同一文件
- 只有情绪判断、没有事实锚点的内容

尤其不要把整段逐字稿直接扔进知识库而不整理。

## 如何新增知识卡片

1. 在对应目录新增 txt：

```text
data/knowledge/sources/geopolitics/
data/knowledge/sources/markets/
data/knowledge/sources/tech/
data/knowledge/sources/general/
```

2. 按上面的卡片结构写内容

3. 运行入库：

```bash
python scripts/run_knowledge_ingest.py
```

4. 如需查看检索效果，再运行：

```bash
python scripts/run_retriever.py
```

## 当前建议

现阶段优先扩充“专题卡片”，而不是继续扩充大而泛的长书全文。

原因是：

- 专题卡片更容易被当前 retriever 命中
- 排序更稳定
- 对后续 expert_agent 更友好

一句话原则：

**让知识库更像“可检索专题卡片集合”，而不是“原始资料堆”。**
