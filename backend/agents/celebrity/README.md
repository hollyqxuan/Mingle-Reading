# Persona Agent Configuration

Mingle Reading 当前的三位名家 agent 已不再依赖旧的 `persona pack / catalog / persona_kb` 目录作为主数据源。

当前唯一的名家资料来源是：

- `backend/assets/Celebrity-skill/Celebrity-skill/LuXun-skill-main`
- `backend/assets/Celebrity-skill/Celebrity-skill/MarkTwain-skill-main`
- `backend/assets/Celebrity-skill/Celebrity-skill/ZhangAiLing-skill-main`

每个 skill bundle 都包含：

- `SKILL.md`
- `README.md`
- `references/research/01-writings.md`
- `references/research/02-conversations.md`
- `references/research/03-expression-dna.md`
- `references/research/04-external-views.md`
- `references/research/05-decisions.md`
- `references/research/06-timeline.md`

## Current Workflow

当前名家 agent 的回答链是三路合成：

1. `Book knowledge`
   - 来自当前书本的正文检索与 temporal knowledge graph 检索
   - 并且始终受阅读进度约束
2. `Celebrity-skill RAG`
   - 从对应名家的 skill bundle 中切出 markdown snippet
   - 按 query 检索 relevant snippets
3. `Persona skill prompt`
   - 由 `SKILL.md` 的角色规则与研究资料共同塑造 system prompt

最终回答遵守：

- 名家可以有完整的人设资料
- 但讨论当前书本时，仍然只能依据当前可见正文和图谱证据
- 不允许剧透未来剧情

## Runtime Configuration

在根目录 `.env` 中为每位 agent 配置：

- `LU_XUN_API_KEY`
- `LU_XUN_BASE_URL`
- `LU_XUN_MODEL_NAME`
- `MARK_TWAIN_API_KEY`
- `MARK_TWAIN_BASE_URL`
- `MARK_TWAIN_MODEL_NAME`
- `ZHANG_AILING_API_KEY`
- `ZHANG_AILING_BASE_URL`
- `ZHANG_AILING_MODEL_NAME`

中性导读可选：

- `MUSE_NEUTRAL_API_KEY`
- `MUSE_NEUTRAL_BASE_URL`
- `MUSE_NEUTRAL_MODEL_NAME`

## API Surface

- `GET /api/personas`
- `GET /api/persona-agents`
- `GET /api/persona-agents/{persona_id}`
- `GET /api/persona-agents/{persona_id}/kb`
- `POST /api/persona-agents/{persona_id}/retrieve`
- `POST /api/persona-agents/{persona_id}/prompt-preview`
- `POST /api/qa`
- `POST /api/summary`

其中：

- `/retrieve` 返回基于 `celebrity-skill` 检索到的 snippets
- `/prompt-preview` 展示当前 persona 的 system prompt 与 skill hits
- `/api/qa` 与 `/api/summary` 使用 `book graph + skill RAG + persona prompt` 联合生成
