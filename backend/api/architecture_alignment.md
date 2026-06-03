# Mingle Reading 架构对齐说明

本文基于现有材料整理，不另起炉灶，重点对齐已经出现过的产品目标、系统模块、数据资产与评测边界。

主要依据：

- `backend/docs/_source_notes/Opening Report v3(1).txt`
- `backend/docs/_source_notes/Opening Report pre.txt`
- `backend/docs/_source_notes/Mingle Reading_数据集构建方案与评测基准设计.txt`
- `backend/docs/_source_notes/数据集构建与模型评测.txt`
- `backend/docs/_source_notes/渐进式节拍记忆系统(PBM)数据集介绍 (1)(1).txt`

## 1. 已提出的系统模块清单

### 1.1 前端交互层

来自 opening report 的稳定表述是基于 `SageRead` 扩展阅读器，而不是从零自建。当前已提出的前端模块包括：

- `Web Reader Interface`：打开书籍、正文渲染、章节导航、滚动定位。
- `Selection/Highlight`：选中文本、创建高亮、触发解释/提问。
- `Chat Sidebar / Mingle Talk`：承载问答、引导式对话、章节总结。
- `Intelligent Bubble / Mingle Spark`：行内提示、角色气泡、主动注释。
- `Notes & Highlights`：保存笔记、高亮、书签、摘录。
- `Reading Progress Tracker`：记录 chapter/section/scroll/dwell time，作为后端的单一进度事实源。

### 1.2 Agent 编排与生成层

- `Multi-Agent Orchestrator`：根据用户动作和上下文路由到对应能力。
- `Lead Reader Agent`：作者/历史人物风格化讲解、评论、章节总结。
- `Book Character Agent`：基于书内角色的设定化对话与第一人称内心气泡。
- `Prompt Scaffold Engine`：把鲁迅、张爱玲、王尔德等 persona 的“分析路径”编码进 system prompt。
- `Memory Manager`：维护对话记忆、persona 边界、阅读进度约束、防剧透控制。
- `Bubble Trigger Engine`：根据情绪显著性和停留时长，决定是否主动触发气泡。

### 1.3 检索、数据与理解层

- `Book Ingestion Pipeline`：原始 EPUB/TXT 采集、清洗、章节解析、语义切片、元数据注入。
- `Author/Persona Corpus Builder`：作者作品、书信、访谈、传记等资料入库。
- `Character Corpus Builder`：从书本文本中构建角色相关上下文与设定信息。
- `Embedding & Vector Store`：向量化与本地向量库管理，候选方案为 `ChromaDB` 或 `Qdrant`。
- `Metadata Filter / Narrative-Aware Gating`：基于阅读进度的硬过滤，先过滤后检索，确保防剧透。
- `Reading Understanding Support`：为解释、讨论、背景补充提供上下文拼装能力。

### 1.4 数据集与评测层

- `Structured RAG Corpus`：书本文本库、作者/角色资料库。
- `Annotation / Golden Dataset`：情绪强度、冲突强度、心理复杂度、象征密度、主动提示适配性。
- `Spoiler-Trap Dataset`：对抗式防剧透评测集。
- `Persona Fidelity Benchmark`：人设一致性评测集。
- `PBM Benchmark`：围绕 `SANQA / ERE / CME` 的渐进式理解评测框架。

## 2. 模块间输入输出定义

详细接口建议见 [system_interfaces.md](/C:/Users/21358/Desktop/MingleReading/architecture/system_interfaces.md)。这里先给系统级数据流。

### 2.1 核心在线链路

1. 前端阅读器输入：
   用户打开书籍、滚动、选中片段、提问、停留。
2. 进度状态输出：
   前端持续产出 `book_id + chapter_id + section_id + paragraph_id/offset + dwell_time`。
3. Agent 编排输入：
   编排层接收 `用户意图 + 选中文本 + 进度状态 + 当前 persona/character`。
4. 检索层输入：
   编排层生成检索请求，携带 `query + retrieval_scope + progress_guard`。
5. 检索层输出：
   返回 `top-k chunks + metadata + citations + filter trace`。
6. 生成层输出：
   生成 `chat reply / bubble / summary / note suggestion`。
7. 前端展示输出：
   以侧边对话、行内气泡、笔记草稿等形式回显。

### 2.2 各模块 I/O 摘要

| 模块 | 主要输入 | 主要输出 |
| --- | --- | --- |
| Web Reader Interface | 书籍文件、用户操作 | 选中文本、问题、进度事件 |
| Progress Tracker | scroll/selection/session 事件 | `reading_progress` |
| Multi-Agent Orchestrator | 用户请求、进度、persona 选择 | 路由决策、生成任务、检索任务 |
| Lead Reader Agent | persona scaffold、检索上下文、用户问题 | 作者视角回答、章节总结、评论 |
| Character Agent | 角色设定、当前阅读上下文、触发类型 | 角色对话、第一人称 bubble |
| Memory Manager | 会话历史、用户偏好、进度状态 | 可注入记忆、边界约束 |
| Bubble Trigger Engine | progress、dwell time、salience score | 是否触发、bubble prompt |
| Ingestion Pipeline | EPUB/TXT、资料源文档 | chunk、metadata、embedding 输入 |
| Vector Store | query、filter | 命中文本块、引用信息 |
| Evaluation Layer | 生成结果、标准答案、标注规则 | 检索、人设、防剧透、PBM 分数 |

## 3. MVP 先做什么 / 不做什么

以下是基于现有材料整理后的“建议版 MVP 边界”，目标是先把最小闭环跑通，而不是一次实现全愿景。

### 3.1 MVP 必做

- 基于 `SageRead` 跑通阅读器骨架，不重写 EPUB 渲染与阅读布局。
- 支持 `打开书籍 -> 选中文本 -> 提问 -> 返回回答` 的最小在线链路。
- 打通 `reading_progress` 单一事实源，并把它注入每次推理请求。
- 实现 `Book Ingestion Pipeline` 的第一版：
  - 书本文本清洗
  - 章节/段落切分
  - 基础 metadata 注入
  - 向量入库
- 实现 `Narrative-Aware Gating` 第一版：
  - 检索前 metadata 过滤
  - prompt 层防剧透约束
- 至少实现 1 个 `Lead Reader Agent` 与 1 个 `Book Character Agent`。
- 支持两种输出形态：
  - `Chat Sidebar`
  - `Intelligent Bubble` 的手动触发版或轻量自动触发版
- 建立最小评测闭环：
  - 检索评测样本
  - 基本人设一致性评测样本
  - 基础防剧透红队样本

### 3.2 MVP 暂不做

- `MEGa` 云端 LoRA 增量训练与权重热加载。
- 完整的长期个性化记忆与跨 session 关系演化。
- 大规模作者矩阵、角色矩阵批量运营。
- 完整的 `PBM` 全套公开 benchmark 发布。
- 复杂 research workspace：引用、提纲、对比写作、知识画像全量能力。
- 多模态扩展、语音、复杂情绪引擎。
- 从头建设独立阅读器前端。

### 3.3 为什么这样切

这样切法与现有材料是对齐的：

- 核心创新已经足够清晰：`persona + RAG + progress-aware anti-spoiler + inline interaction`。
- `MEGa`、PBM 全量评测、研究支持空间都重要，但更像第二阶段放大器，不是第一阶段闭环所必需。
- 只要 MVP 先证明“按阅读进度作答 + 风格化解释 + 轻量主动提示”成立，项目主线就站住了。

## 4. 对 README 友好的系统架构摘要

Mingle Reading 是一个构建在 `SageRead` 阅读器之上的 AI 陪读系统。它以阅读进度为单一事实源，将用户的选中片段、提问、停留行为送入多 Agent 编排层，再通过带元数据过滤的 RAG 检索出当前可见范围内的书本文本、作者资料或角色上下文，生成作者视角讲解、角色对话、章节总结与行内气泡提示。

系统的关键不是“让模型会聊天”，而是把四件事绑定在一起：

- `persona simulation`：作者/角色不是通用助手，而是带分析路径与表达边界的阅读陪伴者。
- `progress-aware retrieval`：检索只允许访问已读范围，防剧透不是口头承诺，而是数据层硬约束。
- `inline reading interaction`：回答不仅出现在聊天侧栏，也能以气泡形式嵌回阅读现场。
- `dataset + evaluation driven`：书本文本库、人物资料库、标注集、评测集共同定义系统上限。

## 5. 需要继续对接的点

- 由前端/集成 Agent 确认 `SageRead` 当前接入方案和真实事件流字段。
- 由数据 Agent 继续把 `chunk metadata schema`、评测样本格式、PBM 数据组织方式落成可执行规范。
- 由模型/后端 Agent 决定首版 `retrieval API`、`orchestrator API` 和 prompt 模板结构。
