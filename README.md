# Mingle Reading

Mingle Reading 是一个面向长篇文学作品的 AI 伴读系统。它以沉浸式阅读器为主界面，将书籍拆解为段落级阅读单元，并围绕阅读进度构建知识图谱、实体注册表、分层记忆和检索索引，支持有证据约束的问答、角色对话、文学导师伴读、行内 Bubble 批注和 3D Memory Map。

系统默认面向普通阅读场景：正文优先展示，书架、上传、记忆重建、角色、图谱和设置等能力通过抽屉与标签页展开，避免阅读时被工程面板打断。

## 系统架构

```text
┌────────────────────────────────────────────────────────┐
│                      前端阅读器                         │
│  沉浸阅读 · Library Drawer · Assistant · Insight Drawer │
│  Character · Memory Map · Inline Bubble · Mobile Nav    │
├────────────────────────────────────────────────────────┤
│                       API / Agent 层                    │
│  文学导师 QA · 章节摘要 · 角色画像/对话 · Bubble 策略     │
│  防剧透 · Evidence Pack · Citation / Claim 输出          │
├────────────────────────────────────────────────────────┤
│                     Memory / Retrieval 层               │
│  Entity Registry · Episode / Chapter Memory             │
│  Character Arc · Theme Arc · 本地 JSON Retrieval Index   │
│  关键词检索 · Embedding 检索 · 图谱邻居扩展               │
├────────────────────────────────────────────────────────┤
│                       知识图谱层                         │
│  Chapter → Episode → Entity → Relation                   │
│  实体消歧 · 关系时间戳 · 原文证据 · 章节可见范围           │
├────────────────────────────────────────────────────────┤
│                       数据管道层                         │
│  TXT/PDF/EPUB 解析 → 章节分割 → 段落 chunk                │
│  图谱构建 → 记忆索引 → runtime JSON 持久化                │
└────────────────────────────────────────────────────────┘
```

## 核心功能

- 支持 `.txt`、`.pdf`、`.epub` 上传，自动解析章节、段落和阅读 chunk。
- 以 `backend/runtime/books/*.json` 为书籍数据源，生成时态知识图谱、实体注册表、分层记忆和本地检索索引。
- 提供沉浸阅读界面：正文居中、顶部进度、章节/段落跳转、右侧 Assistant、左右抽屉和移动端底部导航。
- 提供文学导师模式：可选择鲁迅、马克·吐温、张爱玲或中性导读，对当前已读范围进行问答和章节总结。
- 提供书中人物模式：从当前书的人物候选中选择角色，生成角色画像，并以角色视角进行对话。
- 提供行内 Bubble：系统对高价值文本做浅色标注，鼠标悬停或点击后显示轻量浮窗，不改变正文排版。
- 提供 Memory Map：在 Insight Drawer 中查看当前段落、当前章节或人物关系，支持人物关系优先、全部实体、拖拽旋转、缩放、重置和全屏。
- 提供证据约束的回答：QA response 包含 `citations`、`claims`、`confidence`、`unsupported_claim_count` 和 `retrieval_trace_id`。
- 提供防剧透保护：问答、摘要、角色对话、Bubble 和图谱检索都围绕当前阅读章节组织候选上下文。
- 不依赖外部数据库：图谱、记忆和检索索引均保存在本地 JSON 文件中。

## 数据与记忆结构

| 结构 | 说明 | 默认位置 |
|------|------|----------|
| Book JSON | 书籍元数据、章节、段落 chunk 和候选人物 | `backend/runtime/books/{book_id}.json` |
| Temporal Graph | 章节、叙事 episode、实体、关系、依赖边和证据 | `backend/runtime/graphs/{book_id}.graph.json` |
| Memory Index | Entity Registry、EpisodeMemory、ChapterMemory、CharacterArcMemory、ThemeArcMemory | `backend/runtime/indexes/{book_id}.memory.json` |
| Retrieval Index | 正文、实体、关系和记忆节点的检索文档与 embedding | `backend/runtime/indexes/{book_id}.retrieval.json` |
| Build Logs | 上传、图谱构建和记忆重建过程日志 | `backend/runtime/logs/` |

Entity Registry 中的实体包含 `entity_id`、`canonical_name`、`aliases`、`entity_type`、`first_seen`、`last_seen`、`mentions`、`evidence_spans`、`confidence` 和 `merge_warnings`。关系端点使用实体 ID，记忆节点保留 evidence chunk，便于前端和回答链路回溯依据。

## 快速开始

### 环境要求

- Python 3.10+
- pip

### 安装依赖

建议使用项目内虚拟环境：

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 配置模型 API

复制环境变量模板：

```bash
cp .env.example .env
```

常用配置如下。聊天、抽取和 embedding 均使用 OpenAI-compatible API；缺少 embedding 配置时，系统仍可启动，记忆状态会标记为 `degraded`，检索回退到关键词和图谱。

```env
# Neutral / fallback reader
MUSE_NEUTRAL_API_KEY="your_api_key"
MUSE_NEUTRAL_BASE_URL="https://your-openai-compatible-endpoint/v1"
MUSE_NEUTRAL_MODEL_NAME="your_chat_model"

# Memory embedding index
MINGLE_EMBEDDING_API_KEY="your_api_key"
MINGLE_EMBEDDING_BASE_URL="https://your-openai-compatible-endpoint/v1"
MINGLE_EMBEDDING_MODEL_NAME="your_embedding_model"

# Literary guide agents
LU_XUN_API_KEY="your_api_key"
LU_XUN_BASE_URL="https://your-openai-compatible-endpoint/v1"
LU_XUN_MODEL_NAME="your_chat_model"

MARK_TWAIN_API_KEY="your_api_key"
MARK_TWAIN_BASE_URL="https://your-openai-compatible-endpoint/v1"
MARK_TWAIN_MODEL_NAME="your_chat_model"

ZHANG_AILING_API_KEY="your_api_key"
ZHANG_AILING_BASE_URL="https://your-openai-compatible-endpoint/v1"
ZHANG_AILING_MODEL_NAME="your_chat_model"

# Optional graph extraction runtime
GRAPHITI_EXTRACTOR_API_KEY="your_api_key"
GRAPHITI_EXTRACTOR_BASE_URL="https://your-openai-compatible-endpoint/v1"
GRAPHITI_EXTRACTOR_MODEL_NAME="your_chat_model"
```

### 启动服务

```bash
python main.py
```

打开 [http://127.0.0.1:8000](http://127.0.0.1:8000)。

如果 `8000` 端口已被占用，可以临时使用其他端口：

```bash
uvicorn backend.api.app:app --host 127.0.0.1 --port 8001 --reload
```

## 使用流程

1. 打开首页后进入沉浸阅读界面。
2. 点击 `书库` 打开 Library Drawer，选择已有书籍，或上传 `.txt`、`.pdf`、`.epub`。
3. 上传任务会显示解析、章节分割、图谱构建和持久化进度。
4. 如果书籍缺少记忆或检索索引，在 Library Drawer 中点击 `Rebuild Memory`。
5. 阅读正文时可通过章节选择器和段落选择器定位内容。
6. 点击 `助手` 打开 Assistant，使用文学导师模式向当前文本提问或总结章节。
7. 切换到 `书中人物` 模式，从人物下拉中选择角色，再进行角色对话。
8. 点击 `洞察` 打开 Insight Drawer，可查看文学导师信息、角色画像和 Memory Map。
9. 正文中的浅色标注为 Bubble，悬停或点击可查看简短批注。

## API 概览

| 端点 | 说明 |
|------|------|
| `GET /api/health` | 服务健康检查 |
| `GET /api/books` | 列出可用书籍 |
| `GET /api/books/{book_id}` | 获取书籍章节和段落内容 |
| `POST /api/upload` | 同步上传并处理书籍文件 |
| `POST /api/upload-jobs` | 创建异步上传任务 |
| `GET /api/upload-jobs/{job_id}` | 查询上传任务进度 |
| `GET /api/personas` | 获取前端可选文学导师 |
| `POST /api/qa` | 基于当前阅读范围、图谱和记忆进行问答 |
| `POST /api/summary` | 生成当前章节摘要 |
| `GET /api/books/{book_id}/characters` | 获取当前已读范围内的人物候选 |
| `POST /api/books/{book_id}/characters/profile` | 生成角色画像 |
| `POST /api/books/{book_id}/characters/chat` | 与书中人物对话 |
| `POST /api/books/{book_id}/inline-bubbles` | 行内 Bubble 兼容接口 |
| `POST /api/books/{book_id}/bubbles/candidates` | 获取经过策略过滤的 Bubble 候选 |
| `GET /api/books/{book_id}/graph` | 获取完整图谱数据 |
| `GET /api/books/{book_id}/graph/metadata` | 获取图谱元数据 |
| `GET /api/books/{book_id}/graph/audit` | 查看图谱审计信息 |
| `GET /api/books/{book_id}/graph/view` | 获取图谱可视化数据 |
| `POST /api/books/{book_id}/graph/query` | 查询图谱实体和关系 |
| `POST /api/books/{book_id}/memory/rebuild-jobs` | 创建记忆重建任务 |
| `GET /api/books/{book_id}/memory/rebuild-jobs/{job_id}` | 查询记忆重建任务进度 |
| `GET /api/books/{book_id}/memory/status` | 获取图谱、记忆和 embedding 状态 |
| `GET /api/books/{book_id}/memory/entities` | 查询实体注册表 |
| `GET /api/books/{book_id}/memory/entities/{entity_id}` | 获取实体详情、mentions、证据和关系 |
| `GET /api/books/{book_id}/memory/map` | 获取 Memory Map 数据 |
| `POST /api/orchestrate` | 调试检索编排和 memory trace |

## 仓库结构

```text
backend/
  api/                    FastAPI 端点、请求/响应模型、上传任务
  agents/
    celebrity/            文学导师、persona 配置、问答与检索
    character/            角色候选、角色画像、角色对话和 Bubble
  data_pipeline/          TXT/PDF/EPUB 解析、章节分割和书籍持久化
  knowledge_graph/        时态知识图谱构建、模型、检索和编排
  memory/                 Entity Registry、分层记忆、embedding 索引和 Memory Map
  runtime/                本地书籍、图谱、索引、上传文件和日志
  safety/                 防剧透检测
  tests/                  API、图谱、记忆和检索测试
frontend/
  index.html              沉浸阅读器
  graph.html              独立图谱调试页
  graph-rebuild-snapshot.html
  app.js                  前端状态、API 调用和交互逻辑
  main.css                视觉样式和响应式布局
main.py                   本地开发入口
requirements.txt          Python 依赖
```

## 构建与维护

上传书籍会自动完成文本解析、图谱构建、记忆索引和本地持久化。已有书籍可通过前端的 `Rebuild Memory` 或 API 重建记忆：

```bash
curl -X POST http://127.0.0.1:8000/api/books/{book_id}/memory/rebuild-jobs
```

图谱和记忆产物保存在 `backend/runtime/` 下。该目录用于本地运行时数据，包含书籍 JSON、图谱 JSON、索引 JSON、上传原文和构建日志。

## 测试

```bash
pytest
```

可按模块运行：

```bash
pytest backend/tests/test_memory_api.py
pytest backend/tests/test_graph_retrieval.py
pytest backend/tests/test_relation_schema.py
```

评测脚本位于 `backend/eval/run_eval.py`，用于覆盖伴读问答、防剧透和章节摘要等任务。

## 开源说明

本仓库按可安全开源的结构组织：代码、schema、示例、脚本和合成评测数据可随仓库分发；受版权保护的书籍内容和本地 API 密钥不应提交到公开仓库。
