# Mingle Reading 统一接口约束建议

本文把现有材料中隐含的模块边界收敛成可实现的接口约束，目标是减少多 Agent 并行开发时的耦合和返工。

## 1. 总体原则

- 所有在线请求都必须带 `session_id`、`book_id`、`reading_progress`。
- `reading_progress` 是防剧透与上下文检索的单一事实源，不能由下游模块自行猜测。
- 检索必须是 `filter first, retrieve second`，禁止“先召回后裁剪未来内容”。
- persona 与 character 语料必须物理或逻辑隔离，避免跨 persona 污染。
- 输出必须可追溯，生成结果需要保留引用 chunk 与过滤依据。
- 先定义稳定 JSON 契约，再允许 prompt、模型、向量库实现替换。

## 2. 核心对象建议

### 2.1 `reading_progress`

```json
{
  "book_id": "hongloumeng-cn-v1",
  "chapter_id": 12,
  "section_id": 3,
  "paragraph_id": 41,
  "token_offset": 18234,
  "scroll_offset": 0.63,
  "dwell_seconds": 18,
  "updated_at": "2026-05-13T15:30:00Z"
}
```

约束：

- `chapter_id` 必填，用于最小防剧透门控。
- `section_id`、`paragraph_id`、`token_offset` 至少保留两个精细位置字段。
- `updated_at` 由前端产生，后端只消费不重写。

### 2.2 `selection_context`

```json
{
  "book_id": "hongloumeng-cn-v1",
  "selection_id": "sel_001",
  "selected_text": "......",
  "left_context": "......",
  "right_context": "......",
  "anchor": {
    "chapter_id": 12,
    "section_id": 3,
    "paragraph_id": 41
  }
}
```

约束：

- `selected_text` 不足以单独送模，必须配套左右文。
- `anchor` 必须与 `reading_progress` 可校验一致。

### 2.3 `agent_request`

```json
{
  "request_id": "req_001",
  "session_id": "sess_001",
  "user_id": "local-user",
  "mode": "chat",
  "agent_type": "lead_reader",
  "agent_id": "eileen_chang",
  "book_id": "hongloumeng-cn-v1",
  "question": "这段话为什么这么冷？",
  "selection_context": {},
  "reading_progress": {},
  "preferences": {
    "language": "zh-CN",
    "verbosity": "short",
    "spoiler_guard": true
  }
}
```

约束：

- `mode` 建议固定枚举：`chat | bubble | summary | note_assist`。
- `agent_type` 建议固定枚举：`lead_reader | character | system_support`。
- `preferences.spoiler_guard` 默认应为 `true`。

### 2.4 `retrieval_request`

```json
{
  "request_id": "ret_001",
  "scope": "book_text",
  "kb_id": "hongloumeng-cn-v1-book",
  "query": "这段话为什么这么冷？",
  "selection_context": {},
  "reading_progress": {},
  "filters": {
    "max_chapter_id": 12,
    "character_ids": ["wang_xifeng"],
    "exclude_spoiler_levels": ["future_explicit"]
  },
  "top_k": 6
}
```

约束：

- `scope` 建议固定枚举：`book_text | author_corpus | character_corpus | mixed`。
- `max_chapter_id` 必须来自 `reading_progress.chapter_id`。
- 若为 `mixed` 检索，返回结果必须显式标出 chunk 来源。

### 2.5 `retrieval_chunk`

```json
{
  "chunk_id": "chunk_001",
  "kb_id": "hongloumeng-cn-v1-book",
  "source_type": "book_text",
  "text": "......",
  "score": 0.87,
  "metadata": {
    "book_id": "hongloumeng-cn-v1",
    "chapter_id": 12,
    "section_id": 3,
    "paragraph_id": 41,
    "characters_present": ["wang_xifeng"],
    "theme_tags": ["family-politics"],
    "spoiler_level": "visible",
    "salience_score": 0.79
  }
}
```

约束：

- `metadata` 至少覆盖 `book_id/chapter_id/section_id/paragraph_id/characters_present/spoiler_level`。
- `salience_score` 可选，但如果 Bubble 自动触发已启用则必须存在。

### 2.6 `generation_response`

```json
{
  "response_id": "resp_001",
  "request_id": "req_001",
  "mode": "chat",
  "agent_type": "lead_reader",
  "agent_id": "eileen_chang",
  "content": "......",
  "citations": [
    {
      "chunk_id": "chunk_001",
      "chapter_id": 12,
      "section_id": 3
    }
  ],
  "guardrail_trace": {
    "spoiler_guard": true,
    "max_chapter_id": 12
  }
}
```

约束：

- 所有外显回答都建议带 `citations`。
- `guardrail_trace` 不是给终端用户看，而是为了调试与评测复现。

## 3. 模块边界建议

### 3.1 前端只负责采集，不负责推理

前端边界：

- 采集选中、高亮、停留、滚动、提问。
- 生成 `reading_progress` 与 `selection_context`。
- 展示 chat、bubble、notes。

前端不负责：

- 拼 prompt
- 做 persona 路由
- 判断是否剧透

### 3.2 编排层只做决策，不直接持有知识库实现细节

编排层负责：

- 识别当前请求属于 `lead_reader`、`character`、`bubble` 还是 `summary`。
- 调用检索层并组装生成输入。
- 统一输出格式。

编排层不负责：

- 自己操作向量库存储细节
- 自己维护 chunk schema

### 3.3 检索层只保证“找得到且不过界”

检索层负责：

- metadata 过滤
- 向量召回
- chunk 排序
- citation 返回

检索层不负责：

- persona 风格生成
- UI 输出样式

## 4. 数据 schema 最低统一要求

### 4.1 书本文本 chunk

最低字段：

- `book_id`
- `chapter_id`
- `section_id`
- `paragraph_id`
- `chunk_text`
- `characters_present`
- `theme_tags`
- `spoiler_level`
- `source_uri`
- `embedding_model`
- `dataset_version`

### 4.2 作者/角色资料 chunk

最低字段：

- `persona_id`
- `source_type`
- `fact_or_style`
- `source_uri`
- `quote_span`
- `license`
- `dataset_version`

建议把 `fact` 与 `style` 分层，避免“写作风格材料”和“事实材料”混用。

### 4.3 标注与评测样本

最低字段：

- `sample_id`
- `task_type`
- `book_id`
- `progress_checkpoint`
- `question`
- `gold_reference`
- `label_schema_version`
- `split`

## 5. MVP 接口冻结建议

第一阶段建议优先冻结以下 4 个契约：

1. `reading_progress`
2. `agent_request`
3. `retrieval_request`
4. `generation_response`

原因：

- 这 4 个对象分别卡住前端、编排、检索、输出四个方向。
- 一旦它们稳定，SageRead 接入、RAG 后端、评测脚本可以并行推进。

## 6. 与 PBM / 评测体系的对齐建议

为了和 `SANQA / ERE / CME` 后续评测兼容，在线接口从第一天起就应保留：

- 明确的 `progress_checkpoint`
- 明确的 `retrieval trace`
- 明确的 `agent mode`
- 明确的 `response citation`

这样后续无论是做防剧透、共情质量还是渐进式理解评测，都不需要回头重构主链路。
