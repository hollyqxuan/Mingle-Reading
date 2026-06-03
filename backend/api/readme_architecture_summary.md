# README 架构摘要（可直接复用）

## What Is Mingle Reading

Mingle Reading 是一个构建在 `SageRead` 阅读器之上的 AI 沉浸式陪读系统。它将阅读器中的选中、高亮、提问、停留与阅读进度事件送入多 Agent 编排层，再通过带元数据门控的 RAG 检索返回作者视角讲解、角色对话、章节总结与行内 Bubble 注释。

## Core Architecture

- `Frontend Interaction Layer`
  - Web Reader Interface
  - Chat Sidebar / Mingle Talk
  - Intelligent Bubble / Mingle Spark
  - Notes & Highlights
  - Reading Progress Tracker
- `Agent Orchestration Layer`
  - Multi-Agent Orchestrator
  - Lead Reader Agent
  - Book Character Agent
  - Prompt Scaffold Engine
  - Memory Manager
  - Bubble Trigger Engine
- `Knowledge & Retrieval Layer`
  - Book Ingestion Pipeline
  - Author / Character Corpus Builder
  - Embedding + Vector Store
  - Narrative-Aware Gating
  - Reading Understanding Support
- `Dataset & Evaluation Layer`
  - Structured RAG Corpus
  - Golden Annotation Dataset
  - Spoiler-Trap Dataset
  - Persona Fidelity Benchmark
  - PBM Benchmark (`SANQA / ERE / CME`)

## Why It Is Different

- `Persona-grounded reading`: 输出不是通用问答，而是作者/角色视角下的解释与陪读。
- `Progress-aware anti-spoiler`: 检索先按阅读进度过滤，再做相似度召回。
- `Inline interaction`: 回答不仅出现在聊天栏，也能回到正文现场形成 Bubble 交互。
- `Dataset-driven quality control`: 文本库、人物资料、标注集与评测集共同约束系统质量。

## MVP Scope

首版建议先完成：

- SageRead 集成
- 基础书本文本 ingest + metadata chunking
- Progress-aware RAG
- 1 个 Lead Reader Agent
- 1 个 Book Character Agent
- Chat Sidebar
- 手动触发或轻量自动触发的 Bubble
- 基础检索 / 人设 / 防剧透评测

首版暂不包括：

- MEGa 长期记忆训练
- 大规模 persona 矩阵
- 完整 PBM benchmark 发布
- 完整 research workspace

## Interface Rule

在线链路统一要求每次请求都携带：

- `session_id`
- `book_id`
- `reading_progress`
- `selection_context`
- `agent_type / mode`

更完整的接口建议见 [system_interfaces.md](/C:/Users/21358/Desktop/MingleReading/architecture/system_interfaces.md)。
