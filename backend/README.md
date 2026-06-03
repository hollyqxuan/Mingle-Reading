# Backend — Mingle Reading

Mingle Reading 后端，负责书籍解析、知识图谱构建、Agent 工作流和 API 服务。

## 模块结构

```
backend/
├── api/                     FastAPI 应用 + 端点 + 请求模型
│   ├── app.py               所有 API 路由
│   ├── schemas.py           书籍/角色/QA/图谱 数据模型
│   └── upload_jobs.py       异步上传作业管理
│
├── agents/                  Agent 层
│   ├── celebrity/           名家伴读 Agent
│   │   ├── answering.py      QA 端点逻辑 (图谱知识块构建 + Persona 调用)
│   │   ├── persona_service.py Persona RAG (角色知识库加载/检索/提示词)
│   │   ├── retrieval.py      文本检索 (BM25 关键词)
│   │   └── model_client.py    OpenAI 兼容 API 客户端
│   └── character/            角色对话 Agent
│       └── service.py         角色画像/对话/行内批注
│
├── knowledge_graph/         知识图谱核心
│   ├── builder.py            图谱构建器 (滑动窗口 + 增量实体解析 + CoT 消歧)
│   ├── models.py             数据模型 (Chapter/Episode/Entity/Relation)
│   ├── retrieval.py          图谱检索器 (评分/重排/BFS 扩展)
│   ├── llm_extraction.py     LLM 提取提示词与调用
│   ├── extraction_window.py  滑动窗口构建 (前导上下文 + 窗口分块)
│   ├── relation_schema.py    关系类型注册表
│   ├── storage.py            图谱持久化
│   ├── build_logger.py       构建过程日志
│   └── orchestration/        混合检索编排
│       ├── service.py         OrchestrationService + EntityNetworkResult
│       ├── models.py          检索请求/响应模型
│       └── utils.py           关键词评分 + 工具函数
│
├── data_pipeline/           数据管道
│   ├── ingest/parser.py       TXT/PDF/EPUB 解析 + 章节分割 + 分块
│   └── storage.py             书籍记录持久化
│
├── safety/                  安全模块
│   └── anti_spoiler.py        防剧透关键词检测
│
├── eval/                    评测
│   └── run_eval.py            基准测试运行器
│
├── assets/                  静态资源
│   ├── Celebrity-skill/       名家角色知识库 (Markdown)
│   ├── examples/              演示书籍
│   └── data/                  标注/评测数据集
│
├── tests/                   测试
├── config.py                路径 + .env 加载
└── runtime/                 运行时产物 (自动生成)
    ├── books/                书籍记录 JSON
    ├── graphs/               知识图谱 JSON
    ├── uploads/              上传文件
    └── logs/                 构建日志
```

## 数据流

```
上传文件 (TXT/PDF/EPUB)
  → parser.py: 文本提取 + 章节分割 + 段落分块
  → builder.py: 滑动窗口构建 → LLM 提取实体/关系 → 增量实体解析
  → storage.py: 持久化为 JSON 图谱文件
  → retrieval.py: 图谱检索 (Agent 查询时)
  → answering.py / service.py: Agent 回复生成
```

## 知识图谱构建

构建由 `TemporalGraphBuilder` 驱动，采用**增量式滑动窗口**策略：

1. **窗口分块**：文本按 800 token 窗口划分，每个窗口配 500 token 前导上下文
2. **LLM 提取**：每个窗口独立调用 LLM，提取实体和关系
3. **增量解析**：新实体与已有实体索引对比——别名匹配则合并，否则新建
4. **CoT 消歧**：System Prompt 包含 4 步推理指令，引导 LLM 区分同名跨代角色
5. **行为缓存**：高频角色动态维护身份描述和行为摘要，辅助消歧
6. **后处理**：canonical-as-alias 扫描、人物描述生成、叙事依赖边构建

图谱构建支持断点恢复（`windows.jsonl` 检查点）和增量续建。

## 图谱检索

- **混合检索** (QA)：文本关键词 BM25 + 图谱 6 路并行 → 合并去重 → Top-K
- **实体中心检索** (角色)：直接按 entity_id 拉取全部关系，无截断
- **可见性过滤**：按用户已读章节过滤实体和关系
- **重排序**：类型加权 (relation +0.25, entity +0.15) + 配额截断

## Agent 层

### Celebrity Agent (名家伴读)
用户提问 → 混合检索 → 图谱知识格式化 → Persona RAG (名家风格片段) → LLM 回复

### Character Agent (角色扮演)
用户提问 → 实体中心检索 → 角色画像生成 → 关系分组 (Family/Interactions/Evidence) → LLM 第一人称回复

### Inline Bubbles (行内批注)
当前页段落 → LLM 生成锚点词 + 标签 + 短评 → 嵌入原文 HTML

## 添加新功能

1. 新 API 端点在 `api/app.py` 注册
2. 新 Agent 逻辑在 `agents/` 下新建模块
3. 图谱检索逻辑在 `knowledge_graph/retrieval.py` 扩展
4. 新关系类型在 `relation_schema.py` 注册
5. 消歧方法通过 `TemporalGraphBuilder` 的特征开关控制
