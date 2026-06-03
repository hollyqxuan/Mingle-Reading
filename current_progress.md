# Mingle Reading — Current Progress

> Last updated: 2026-05-19  
> Knowledge graph for 百年孤独 (One Hundred Years of Solitude): **complete**

## Project Overview

Mingle Reading is a literary reading platform that builds temporal knowledge graphs from uploaded books (EPUB/PDF/TXT) and provides AI-powered reading assistants (literary agents, character agents) backed by structured graph knowledge.

## What Works

### Knowledge Graph Building (`backend/knowledge_graph/`)

- **LLM-only extraction** — all heuristic/regex-based extraction removed. Every episode chunk gets an LLM call. No English keyword gate, no `\b[A-Z][a-z]{2,}\b` regex noise.
- **Chinese-optimized prompt** (`llm_extraction.py:227-265`) — system prompt rewritten in Chinese with:
  - 5 narrative signals for character disambiguation (age/generation, birth/appearance, kinship terms, physical description, behavioral roles)
  - Mandatory `resolution_hint` field per entity explaining disambiguation decisions
  - "When uncertain, split" rule (宁分不合) for novels with shared names across generations
  - Explicit entity type guidance — no default fallback to `character`
  - Rule to ignore English annotations/footnotes
- **Entity types**: character, location, group, artifact, concept — `unknown` is the fallback, never `character`
- **Relation types**: FAMILY_OF, LOCATED_IN, SPOKE_WITH, CONFLICTS_WITH, CARES_ABOUT, MEMBER_OF, ACCOMPANIES, OWNS
- **Stateful edge invalidation**: location/membership/status relations track valid_at_chapter and invalid_at_chapter
- **Post-processing**: chapter consolidation → community building (BFS) → saga building → chapter timeline
- **Incremental save + resume** — graph saved to `runtime/graphs/{book_id}.graph.json` after every episode. On restart, loads partial graph, rebuilds all tracking state, skips already-processed chunks.
- **Build logger** (`build_logger.py`) — structured JSONL log at `runtime/logs/{book_id}.build.jsonl` with per-episode entity names, relation facts, LLM gate decisions, raw responses

### API (`backend/api/app.py`)

- `POST /api/upload` / `POST /api/upload-jobs` — book upload with background graph building
- `GET /api/books` — list all books
- `GET /api/books/{book_id}` — book detail with chapter info
- `GET /api/books/{book_id}/characters` — character candidates **from knowledge graph** (not heuristic regex)
- `POST /api/books/{book_id}/characters/profile` / `chat` — character agent
- `POST /api/qa` — literary agent QA with **structured graph knowledge** injected into LLM prompt
- `GET /api/books/{book_id}/graph/view` — graph data with `chapter`, `from_chapter`, `limit` params for time filtering, returns nodes, edges, communities, sagas

### Frontend

- **Main reading UI** (`index.html` + `app.js`) — chapter navigation, paragraph reading, character select, literary/character agent chat
- **Character list** — sourced from knowledge graph entities (filtered to `character` type), sorted by mention count, with chapter spans
- **3D Knowledge Graph** (`graph.html`) — standalone page at `/static/graph.html`:
  - Three.js force-directed 3D layout with orbit controls
  - Raycasting hover tooltips showing entity/relation details
  - Click-to-inspect sidebar with full entity/relation info
  - Chapter range slider (From Ch / To Ch) for temporal filtering
  - Communities and sagas panel with summaries
  - Node colors by entity type, sized by mention count
  - Edge opacity shows active vs invalidated status

### LLM Infrastructure

- **Model**: `deepseek-v4-flash` via OpenAI-compatible API at `https://api.deepseek.com`
- **Thinking disabled** — `"thinking": {"type": "disabled"}` in payload prevents reasoning tokens from consuming output budget
- **max_tokens**: 8192 for extraction, 4000 for other calls
- **Retry logic**: 3 attempts with backoff, retryable markers include "eof", "ssl", "remote end closed connection", "unexpected"
- **`reasoning_content` NOT used** — reverted fallback that was returning thinking text instead of JSON
- **ConnectionError handling** — `RemoteDisconnected` and `OSError` caught and wrapped as retryable RuntimeError

### 百年孤独 Knowledge Graph

| Metric | Count |
|---|---|
| Episodes | 838 |
| Entities | 196 (119 characters, 30 locations, 18 groups, 24 artifacts, 5 concepts) |
| Relations | 193 (181 active, 12 invalidated) |
| Communities | 103 |
| Sagas | 4 |
| Timeline entries | 23 (one per chapter) |
| Graph file | 4.9MB |

**Top characters**: 何塞·阿尔卡蒂奥·布恩迪亚 (39), 蕾梅黛丝 (32), 乌尔苏拉·伊瓜兰 (22), 奥雷里亚诺·布恩迪亚上校 (20)

**Relation types**: FAMILY_OF (49), CARES_ABOUT (38), SPOKE_WITH (34), LOCATED_IN (25), CONFLICTS_WITH (17), ACCOMPANIES (13), OWNS (11), MEMBER_OF (4), VISITED (2)

## Known Issues

### Entity Resolution Quality

- **Father/son merge in 百年孤独**: `何塞·阿尔卡蒂オ·布恩迪ア` (patriarch) and `何塞·アルカティオ` (firstborn son) were merged into one entity because the LLM dropped the distinguishing surname "布恩迪亚" in c001-p030. The new prompt's disambiguation rules should prevent this on rebuild.
- **`堂何塞·アルカティオ·ブエンディア` separated from main entity** — the honorific "堂" (Don) defeated slug-based alias matching.
- **Over-merged aliases**: The patriarch's entity has `['父亲', '大儿子', '哥哥', '巨汉', '堂费尔南多', '外祖父']` — roles from multiple characters collapsed into one.

### Entity Type Misclassification

- 24 entities were manually corrected from `character` to `artifact` (objects like 磁石, 盔甲, 放大镜 classified as characters). Fixed in the stored graph JSON.
- The new prompt emphasizes strict type classification — should prevent this on rebuild.

### Missing Features

- **Non-stateful relation time tracking**: Only `location/membership/status` relations get `invalid_at_chapter`. `SPOKE_WITH`, `CARES_ABOUT`, etc. stay active forever.
- **Character agent doesn't use knowledge graph**: `character/service.py` uses TF-IDF text search only, never queries graph entities or relations.
- **Post-processing not triggered on resume**: Communities/sagas/timeline are only built at the end of `build()`. If the build resumes from checkpoint, the post-processing needs to be manually triggered (as done for the 百年孤独 graph).

## File Structure (key files)

```
MingleReading/
├── backend/
│   ├── api/app.py                    # FastAPI routes
│   ├── config.py                     # RUNTIME_DIR, GRAPHS_DIR, LOGS_DIR
│   ├── knowledge_graph/
│   │   ├── builder.py                # TemporalGraphBuilder (LLM-only, incremental save, resume)
│   │   ├── models.py                 # Pydantic graph models
│   │   ├── llm_extraction.py         # Chinese prompt, LLM caller
│   │   ├── build_logger.py           # JSONL build logger
│   │   ├── storage.py                # save_graph/load_graph
│   │   ├── retrieval.py              # TemporalGraphRetriever
│   │   └── orchestration/service.py  # OrchestrationService
│   ├── agents/
│   │   ├── character/service.py      # Character candidates from graph
│   │   ├── celebrity/answering.py    # QA with structured graph knowledge
│   │   ├── celebrity/persona_service.py
│   │   └── celebrity/model_client.py # HTTP caller with thinking disabled
│   ├── data_pipeline/ingest/parser.py # EPUB parsing, chunking
│   └── runtime/
│       ├── books/                    # BookRecord JSON files
│       ├── graphs/                   # TemporalContextGraph JSON files
│       ├── logs/                     # Build JSONL logs
│       └── uploads/                  # Original uploaded files
├── frontend/
│   ├── index.html                    # Main reading SPA
│   ├── app.js                        # Frontend logic
│   ├── graph.html                    # Standalone 3D graph visualization
│   └── main.css
├── .env                              # API keys (NOT committed)
├── requirements.txt
└── current_progress.md               # This file
```

## Environment

- **Docker container**: `claude-code-deepseek-container`
- **LLM**: deepseek-v4-flash via `https://api.deepseek.com` (OpenAI-compatible)
- **Graph extractor**: GRAPHITI_EXTRACTOR_* env vars in `.env`
- **Python**: 3.12, dependencies: fastapi, uvicorn, pydantic, pypdf
