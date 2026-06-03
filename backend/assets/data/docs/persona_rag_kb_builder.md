# 角色 RAG 知识库构建器

本文档描述了将三位领读角色目录和角色包转换为可检索的传统 RAG 知识库骨架的本地构建器。

## 目标

构建器将角色资产准备为可用于 prompt 注入和轻量级检索的格式，而无需将角色节点放入时间图数据库。

当前支持的角色：

- `persona_lu_xun`
- `persona_mark_twain`
- `persona_zhang_ailing`

## 输入

构建器读取两类现有资产：

- `backend/assets/data/raw/persona_sources/catalog_<persona>__v001.json`
  - 来源清单，分为：
    - `works`（作品）
    - `voice_sources`（声音来源）
    - `biography_and_critical`（传记与评论）
- `backend/assets/data/processed/personas/persona_<name>__v*.json`
  - 符合 schema 的角色包，包含：
    - `fact_layer`（事实层）
    - `style_layer`（风格层）
    - `stance_layer`（立场层）
    - `source_layer`（来源层）
    - `constraints`（约束）

## 输出

对于每个角色，构建器在 `backend/assets/data/processed/personas/persona_kb/<persona_id>/` 下写入一个目录：

- `documents.jsonl`
  - 一条 `persona_profile` 文档
  - 每个目录条目一条 `source_document`
- `retrieval_snippets.jsonl`
  - 用于风格、立场、主题和边界的角色包片段
  - 每个目录条目一条 `source_overview` 片段
- `manifest.json`
  - 输入引用
  - 输出文件引用
  - 分类计数
  - 检索说明

## 检索用法

推荐检索顺序：

1. 从书籍侧 RAG 或时间图中获取当前阅读器可见的书籍上下文。
2. 当任务需要声音、立场或风格控制时，从 `persona_pack` 中检索角色片段。
3. 当任务是"此角色会如何评论这段落？"时，优先检索 `voice_sources`。
4. 将 `works` 和 `biography_and_critical` 用作背景和反复出现的主题的支撑证据。

## 运行

```bash
python backend/scripts/persona_rag_kb_builder.py
```

构建单个角色：

```bash
python backend/scripts/persona_rag_kb_builder.py --persona-id persona_lu_xun
```

## 备注

- 构建器不使用也不存储 API key。
- 知识库有意以摘要为基础；不尝试发布完整的受版权保护文本。
- `voice_sources` 是领读角色 prompting 最高价值的桶，因为它捕捉了语调、推理节奏和自我定位。
