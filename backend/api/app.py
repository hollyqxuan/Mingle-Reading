from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from threading import Thread

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend.api.upload_jobs import upload_job_registry
from backend.data_pipeline.ingest.parser import (
    SUPPORTED_UPLOAD_SUFFIXES,
    UploadTextExtractionError,
    UnsupportedUploadFormatError,
    build_book_record,
    build_book_record_from_upload,
    read_uploaded_text,
    slugify,
)
from backend.data_pipeline.storage import list_books, load_book, save_book
from backend.config import EXAMPLES_DIR, ROOT_DIR, UPLOADS_DIR
from backend.api.schemas import (
    CharacterChatRequest,
    CharacterProfileRequest,
    InlineBubbleRequest,
    PersonaPromptPreviewRequest,
    PersonaRAGQueryRequest,
    QuestionRequest,
    SummaryRequest,
    UploadResponse,
)
from backend.agents.character.service import (
    answer_as_character,
    generate_character_profile,
    generate_inline_bubbles,
    list_character_candidates,
)
from backend.knowledge_graph.builder import TemporalGraphBuilder, build_temporal_graph
from backend.knowledge_graph.build_logger import GraphBuildLogger
from backend.knowledge_graph.models import GraphQuery
from backend.knowledge_graph.retrieval import TemporalGraphRetriever
from backend.knowledge_graph.storage import load_graph, load_graph_metadata, save_graph
from backend.agents.celebrity.answering import build_answer
from backend.knowledge_graph.orchestration.service import OrchestrationService
from backend.memory.service import (
    build_memory_index,
    entity_detail,
    ensure_memory_index,
    list_entities,
    memory_job_registry,
    memory_map,
    memory_status,
    rebuild_memory_from_book,
    save_memory_index,
)
from backend.agents.celebrity.persona_service import (
    PersonaAgentConfigurationError,
    PersonaAgentInvocationError,
    build_persona_prompt_preview,
    get_persona_agent,
    get_persona_kb_manifest,
    list_persona_agents,
    list_personas,
    retrieve_persona_snippets,
)
from backend.agents.celebrity.chapter_summary import summarize_chapter


app = FastAPI(title="Mingle Reading MVP", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

frontend_dir = ROOT_DIR / "frontend"
app.mount("/static", StaticFiles(directory=frontend_dir), name="static")


def _upload_stage_percent(stage: str) -> int:
    mapping = {
        "queued": 0,
        "extract-source-text": 5,
        "segment-chapters": 12,
        "construct-episodes": 22,
        "graph-episode-start": 30,
        "llm-skipped": 40,
        "llm-request-dispatched": 46,
        "llm-response-received": 56,
        "llm-request-failed": 56,
        "graph-episode-complete": 66,
        "chapter-consolidation": 76,
        "graph-dependency-build": 84,
        "graph-finalize": 88,
        "graph-timeline-build": 92,
        "graph-build-finished": 95,
        "persist-book-record": 97,
        "persist-graph-snapshot": 99,
        "finalize-upload": 99,
        "completed": 100,
        "failed": 100,
    }
    return mapping.get(stage, 0)


def _update_upload_job(job_id: str, **fields):
    stage = fields.get("stage")
    if stage and "percent" not in fields:
        fields["percent"] = _upload_stage_percent(stage)
    upload_job_registry.update(job_id, **fields)


def _process_upload_job(job_id: str, *, original_name: str, suffix: str, raw_bytes: bytes) -> None:
    _update_upload_job(
        job_id,
        status="running",
        stage="extract-source-text",
        title="Extracting source text",
        message=f"Reading uploaded file {original_name} and extracting source text.",
    )
    try:
        text = read_uploaded_text(original_name, raw_bytes)
        title = Path(original_name).stem
        safe_name = slugify(title)
        upload_path = UPLOADS_DIR / f"{safe_name}{suffix}"
        if suffix in SUPPORTED_UPLOAD_SUFFIXES - {".txt"}:
            upload_path.write_bytes(raw_bytes)
        else:
            upload_path.write_text(text, encoding="utf-8")

        _update_upload_job(
            job_id,
            stage="segment-chapters",
            title="Segmenting chapters and paragraphs",
            message="Segmenting the uploaded text into chapter and paragraph episodes.",
            book_title=title,
        )

        def parser_progress(payload: dict) -> None:
            _update_upload_job(job_id, **payload)

        record = build_book_record_from_upload(
            title=title,
            filename=original_name,
            raw_bytes=raw_bytes if suffix != ".txt" else text.encode("utf-8"),
            source_path=upload_path,
            progress_callback=parser_progress,
        )
        _update_upload_job(
            job_id,
            book_id=record.book_id,
            book_title=record.title,
            chunk_count=len(record.chunks),
            chapter_count=record.chapter_count,
            total_snippets=len(record.chunks),
        )

        def graph_progress(payload: dict) -> None:
            _update_upload_job(job_id, **payload)

        build_log = GraphBuildLogger(book_id=record.book_id, title=record.title)
        graph = TemporalGraphBuilder(
            progress_callback=graph_progress,
            strict_llm_extraction=True,
            build_logger=build_log,
        ).build(record)

        _update_upload_job(
            job_id,
            stage="persist-book-record",
            title="Persisting book record",
            message="Persisting the parsed book record.",
            processed_snippets=len(record.chunks),
            total_snippets=len(record.chunks),
        )
        save_book(record)

        _update_upload_job(
            job_id,
            stage="persist-graph-snapshot",
            title="Persisting graph snapshot",
            message="Persisting the temporal graph snapshot, including entities, relations, and dependency edges.",
            processed_snippets=len(record.chunks),
            total_snippets=len(record.chunks),
            details={
                "entity_count": len(graph.entities),
                "relation_count": len(graph.relations),
                "dependency_edge_count": sum(len(ep.depends_on) for ep in graph.episodes.values()),
            },
        )
        save_graph(graph)
        save_memory_index(build_memory_index(record, graph))

        _update_upload_job(
            job_id,
            status="completed",
            stage="completed",
            title="Temporal graph ready",
            message=f"Temporal graph ready for {record.title}.",
            processed_snippets=len(record.chunks),
            total_snippets=len(record.chunks),
            book_id=record.book_id,
            book_title=record.title,
            chunk_count=len(record.chunks),
            chapter_count=record.chapter_count,
        )
    except UnsupportedUploadFormatError as exc:
        _update_upload_job(
            job_id,
            status="failed",
            stage="failed",
            title="Upload failed",
            message=str(exc),
            error=str(exc),
        )
    except UploadTextExtractionError as exc:
        _update_upload_job(
            job_id,
            status="failed",
            stage="failed",
            title="Text extraction failed",
            message=str(exc),
            error=str(exc),
        )
    except Exception as exc:
        _update_upload_job(
            job_id,
            status="failed",
            stage="failed",
            title="Temporal graph build failed",
            message=f"Temporal graph build failed: {exc}",
            error=str(exc),
        )


def ensure_demo_book_loaded() -> None:
    demo_path = EXAMPLES_DIR / "mingle_demo_book.txt"
    if not demo_path.exists():
        return
    book_id = slugify(demo_path.stem)

    try:
        load_book(book_id)
    except FileNotFoundError:
        record = build_book_record(
            title=demo_path.stem,
            raw_text=demo_path.read_text(encoding="utf-8"),
            source_path=demo_path,
        )
        save_book(record)
    else:
        record = None

    try:
        load_graph(book_id)
    except FileNotFoundError:
        if record is None:
            record = load_book(book_id)
        save_graph(build_temporal_graph(record))


def get_or_build_book(book_id: str):
    try:
        record = load_book(book_id)
    except FileNotFoundError:
        demo_path = EXAMPLES_DIR / f"{book_id}.txt"
        if not demo_path.exists():
            raise
        record = build_book_record(
            title=demo_path.stem,
            raw_text=demo_path.read_text(encoding="utf-8"),
            source_path=demo_path,
        )
        save_book(record)
    if record.chunks:
        return record
    source_path = Path(record.source_path)
    if not source_path.exists():
        return record
    rebuilt = build_book_record_from_upload(
        title=record.title,
        filename=source_path.name,
        raw_bytes=source_path.read_bytes(),
        source_path=source_path,
    )
    if rebuilt.chunks:
        save_book(rebuilt)
        save_graph(build_temporal_graph(rebuilt))
        return rebuilt
    return record


def get_or_build_graph(book_id: str):
    try:
        return load_graph(book_id)
    except FileNotFoundError:
        book = get_or_build_book(book_id)
        graph = build_temporal_graph(book)
        save_graph(graph)
        return graph


def _process_memory_rebuild_job(job_id: str, book_id: str) -> None:
    try:
        book = get_or_build_book(book_id)

        def graph_progress(payload: dict) -> None:
            stage = str(payload.get("stage", "graph-rebuild"))
            processed = int(payload.get("processed_snippets", 0) or 0)
            total = int(payload.get("total_snippets", 0) or 0)
            percent = 25
            if total:
                percent = min(70, 20 + int(processed / total * 50))
            memory_job_registry.update(
                job_id,
                status="running",
                stage=stage,
                title=str(payload.get("title", "Rebuilding graph")),
                message=str(payload.get("message", "Rebuilding memory graph from book text.")),
                percent=percent,
                details=dict(payload.get("details", {})),
            )

        graph_builder = TemporalGraphBuilder(
            progress_callback=graph_progress,
            strict_llm_extraction=False,
            build_logger=GraphBuildLogger(book_id=book.book_id, title=book.title),
        )
        rebuild_memory_from_book(book, graph_builder=graph_builder, job_id=job_id)
    except Exception as exc:
        memory_job_registry.update(
            job_id,
            status="failed",
            stage="failed",
            title="Memory rebuild failed",
            message=str(exc),
            percent=100,
            error=str(exc),
        )


@app.on_event("startup")
def startup_event() -> None:
    ensure_demo_book_loaded()


@app.get("/")
def root() -> FileResponse:
    return FileResponse(frontend_dir / "index.html")


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/books")
def books() -> list[dict[str, str]]:
    return list_books()


@app.get("/api/personas")
def personas():
    return [persona.model_dump() for persona in list_personas()]


@app.get("/api/frontend1/categories")
def frontend1_categories():
    return [
        {"category_id": "favorites", "name": "收藏", "href": "favorites.html", "cover": "cateCard_collect.png"},
        {"category_id": "suspense", "name": "悬疑", "href": "", "cover": "cateCard_suspense.png"},
        {"category_id": "sci-fi", "name": "科幻", "href": "", "cover": "cateCard_sci-fi.png"},
        {"category_id": "prose", "name": "散文", "href": "", "cover": "cateCard_prose.png"},
        {"category_id": "lovestory", "name": "爱情", "href": "", "cover": "cateCard_lovestory.png"},
    ]


@app.get("/api/frontend1/categories/{category_id}/books")
def frontend1_category_books(category_id: str):
    books = [
        {
            "book_id": "the-pig-like-maverick",
            "title": "特立独行的猪",
            "cover": "bookCover_aPig.png",
            "badge": "bookCornerBadge_Luxun.png",
            "has_notification": True,
            "href": "aPig.html",
        },
        {
            "book_id": "one-hundred-years-of-solitude",
            "title": "百年孤独",
            "cover": "bookCover_100yrsofSolitude.png",
            "badge": "bookCornerBadge_Eileen.png",
            "has_notification": False,
            "href": "100yrs.html",
        },
        {"book_id": "guxiang", "title": "故乡", "cover": "bookCover_Guxiang.png", "href": "#"},
        {"book_id": "marcovaldo", "title": "马可瓦多", "cover": "bookCover_MarcoValdo.png", "href": "#"},
    ]
    titles = {
        "favorites": "收藏",
        "suspense": "悬疑",
        "sci-fi": "科幻",
        "prose": "散文",
        "lovestory": "爱情",
    }
    return {"category_id": category_id, "title": titles.get(category_id, "书籍"), "books": books}


@app.get("/api/persona-agents")
def persona_agents():
    return [agent.model_dump() for agent in list_persona_agents()]


@app.get("/api/persona-agents/{persona_id}")
def persona_agent_detail(persona_id: str):
    return get_persona_agent(persona_id).model_dump()


@app.get("/api/persona-agents/{persona_id}/kb")
def persona_agent_kb(persona_id: str):
    return get_persona_kb_manifest(persona_id)


@app.post("/api/persona-agents/{persona_id}/retrieve")
def persona_agent_retrieve(persona_id: str, request: PersonaRAGQueryRequest):
    return [hit.model_dump() for hit in retrieve_persona_snippets(persona_id, request)]


@app.post("/api/persona-agents/{persona_id}/prompt-preview")
def persona_agent_prompt_preview(persona_id: str, request: PersonaPromptPreviewRequest):
    return build_persona_prompt_preview(persona_id, request).model_dump()


@app.get("/api/books/{book_id}")
def book_detail(book_id: str):
    book = get_or_build_book(book_id)
    chapters: dict[int, list[dict[str, str | int]]] = {}
    chapter_titles: dict[int, str] = {}
    for chunk in book.chunks:
        metadata = chunk.metadata or {}
        chapter_title = metadata.get("chapter_title")
        if isinstance(chapter_title, str) and chapter_title.strip():
            chapter_titles.setdefault(chunk.chapter_index, chapter_title.strip())
        chapters.setdefault(chunk.chapter_index, []).append(
            {
                "chunk_id": chunk.chunk_id,
                "paragraph_index": chunk.paragraph_index,
                "text": chunk.text,
            }
        )
    return {
        "book_id": book.book_id,
        "title": book.title,
        "chapter_count": book.chapter_count,
        "chapter_titles": chapter_titles,
        "chapters": chapters,
    }


@app.get("/api/books/{book_id}/characters")
def book_characters(book_id: str, current_chapter: int = 1, limit: int = 10):
    try:
        book = get_or_build_book(book_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="book_not_found") from exc
    try:
        return [item.model_dump() for item in list_character_candidates(book, current_chapter, limit=limit)]
    except (PersonaAgentConfigurationError, PersonaAgentInvocationError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/books/{book_id}/characters/profile")
def character_profile(book_id: str, request: CharacterProfileRequest):
    try:
        book = get_or_build_book(book_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="book_not_found") from exc
    try:
        return generate_character_profile(book, request.character_name, request.current_chapter).model_dump()
    except (PersonaAgentConfigurationError, PersonaAgentInvocationError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/books/{book_id}/characters/chat")
def character_chat(book_id: str, request: CharacterChatRequest):
    try:
        book = get_or_build_book(book_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="book_not_found") from exc
    try:
        return answer_as_character(
            book,
            character_name=request.character_name,
            question=request.question,
            current_chapter=request.current_chapter,
            conversation_history=request.conversation_history,
            top_k=request.top_k,
        ).model_dump()
    except (PersonaAgentConfigurationError, PersonaAgentInvocationError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/books/{book_id}/inline-bubbles")
def inline_bubbles(book_id: str, request: InlineBubbleRequest):
    try:
        book = get_or_build_book(book_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="book_not_found") from exc
    try:
        return [
            item.model_dump()
            for item in generate_inline_bubbles(
                book,
                current_chapter=request.current_chapter,
                visible_chunk_ids=request.visible_chunk_ids,
                persona_id=request.persona_id,
                assistant_mode=request.assistant_mode,
                character_name=request.character_name,
                max_bubbles=request.max_bubbles,
            )
        ]
    except (PersonaAgentConfigurationError, PersonaAgentInvocationError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/books/{book_id}/graph")
def graph_detail(book_id: str):
    graph = get_or_build_graph(book_id)
    return {
        "graph_id": graph.graph_id,
        "book_id": graph.book_id,
        "title": graph.title,
        "graph_version": graph.graph_version,
        "stats": graph.stats().model_dump(),
        "metadata": graph.metadata,
        "chapters": [chapter.model_dump() for chapter in graph.chapters.head(10)],
        "chapter_timeline": [item.model_dump() for item in graph.chapter_timeline[:10]],
        "episodes": [episode.model_dump() for episode in graph.episodes.head(10)],
        "entities": [entity.model_dump() for entity in graph.entities.head(20)],
        "relations": [relation.model_dump() for relation in graph.relations.head(20)],
        "communities": [],
        "sagas": [],
    }


@app.get("/api/books/{book_id}/wordcloud")
def book_wordcloud(book_id: str, limit: int = 36):
    graph = get_or_build_graph(book_id)
    normalized_limit = max(8, min(limit, 80))
    allowed_types = {"character", "location", "artifact", "group", "theme", "concept"}
    stop_words = {
        "unknown",
        "none",
        "人物",
        "地点",
        "概念",
        "主题",
        "关系",
        "故事",
        "文本",
        "章节",
        "叙事",
        "一个",
        "一种",
        "一些",
        "自己",
        "我们",
        "他们",
        "这个",
        "那个",
        "没有",
        "什么",
        "可以",
        "不是",
        "因为",
        "所以",
        "知道",
        "觉得",
        "别人",
        "假如",
        "当然",
        "东西",
        "件事",
        "但我",
        "而且",
        "后来",
        "现在",
        "时候",
        "先生",
        "起来",
        "但是",
        "the",
        "and",
        "for",
        "with",
        "that",
    }

    words = []
    seen: set[str] = set()
    source = "knowledge_graph_entities"
    for entity in sorted(graph.entities.values(), key=lambda item: item.mention_count, reverse=True):
        text = entity.canonical_name.strip()
        if not text or text in seen:
            continue
        if entity.entity_type not in allowed_types:
            continue
        if entity.mention_count <= 0:
            continue
        if text.lower() in stop_words or text.isdigit():
            continue
        if len(text) <= 1:
            continue
        seen.add(text)
        words.append(
            {
                "text": text,
                "weight": entity.mention_count,
                "type": entity.entity_type,
                "summary": entity.summary,
            }
        )
        if len(words) >= normalized_limit:
            break

    if not words:
        source = "book_text_frequency"
        book = get_or_build_book(book_id)
        counter: Counter[str] = Counter()
        blocked_chars = set("的一是在了和与及或也都就而并把被对从中为有无不说这那其我你他她它们可但什")
        for chunk in book.chunks:
            text = chunk.text or ""
            for run in re.findall(r"[\u4e00-\u9fff]{2,80}", text):
                for size in (2, 3, 4):
                    if len(run) < size:
                        continue
                    for index in range(0, len(run) - size + 1):
                        token = run[index:index + size]
                        if token[0] in blocked_chars or token[-1] in blocked_chars:
                            continue
                        if any(char in blocked_chars for char in token):
                            continue
                        counter[token] += 1 + (size - 2) * 0.18
            counter.update(word.lower() for word in re.findall(r"[A-Za-z][A-Za-z'-]{2,}", text))

        for text, count in counter.most_common(normalized_limit * 4):
            if text in seen or text.lower() in stop_words or text.isdigit():
                continue
            if len(text) <= 1:
                continue
            seen.add(text)
            words.append(
                {
                    "text": text,
                    "weight": count,
                    "type": "text_frequency",
                    "summary": "",
                }
            )
            if len(words) >= normalized_limit:
                break

    return {
        "book_id": graph.book_id,
        "title": graph.title,
        "source": source,
        "words": words,
    }


@app.get("/api/books/{book_id}/signature-lines")
def book_signature_lines(book_id: str, limit: int = 18):
    book = get_or_build_book(book_id)
    try:
        graph = load_graph(book_id)
    except FileNotFoundError:
        graph = None
    normalized_limit = max(8, min(limit, 36))
    sentence_limit = min(3, normalized_limit)
    phrase_limit = normalized_limit - sentence_limit

    style_terms = {
        "自由",
        "孤独",
        "思维",
        "命运",
        "荒诞",
        "权力",
        "尊严",
        "沉默",
        "时间",
        "记忆",
        "家族",
        "战争",
        "预言",
        "生活",
        "乐趣",
        "知识",
        "精神",
        "世界",
        "幸福",
        "痛苦",
        "爱情",
        "死亡",
        "希望",
        "秩序",
    }
    weak_terms = {
        "我们",
        "自己",
        "可以",
        "什么",
        "知道",
        "觉得",
        "别人",
        "当然",
        "所以",
        "因为",
        "但是",
        "这个",
        "那个",
        "他们",
        "起来",
        "时候",
    }
    semantic_phrase_terms = {
        "特立独行",
        "思维乐趣",
        "独立思考",
        "精神自由",
        "自由意志",
        "知识分子",
        "荒诞现实",
        "沉默权力",
        "生活尊严",
        "自我驯服",
        "马孔多",
        "布恩迪亚家族",
        "百年孤独",
        "魔幻现实",
        "家族记忆",
        "时间循环",
        "命运预言",
        "孤独宿命",
        "战争记忆",
        "文明幻灭",
        "爱情孤独",
        "历史回声",
    }
    punctuation_pattern = re.compile(r"(?<=[。！？!?])")
    entity_weights = {
        entity.canonical_name: max(1, entity.mention_count)
        for entity in (graph.entities.values() if graph else [])
        if entity.canonical_name and entity.mention_count > 0
    }
    max_entity_weight = max(entity_weights.values(), default=1)
    chunk_entity_counts: dict[str, int] = {}
    for episode in (graph.episodes.values() if graph else []):
        if episode.chunk_id:
            chunk_entity_counts[episode.chunk_id] = max(
                chunk_entity_counts.get(episode.chunk_id, 0),
                len(episode.entity_ids),
            )

    def clean_text(text: str) -> str:
        return re.sub(r"\s+", " ", text).strip()

    def split_sentences(text: str) -> list[str]:
        pieces = punctuation_pattern.split(clean_text(text))
        return [piece.strip() for piece in pieces if piece and piece.strip()]

    def sentence_score(sentence: str, chunk_id: str, chapter_index: int, paragraph_index: int) -> float:
        length = len(sentence)
        if length < 10 or length > 92:
            return -20.0
        length_score = 1.0 - min(abs(length - 34) / 58, 1.0)
        style_score = sum(1.0 for term in style_terms if term in sentence)
        weak_penalty = sum(0.42 for term in weak_terms if term in sentence)
        entity_score = 0.0
        for name, weight in entity_weights.items():
            if name in sentence:
                entity_score += 0.8 + min(weight / max_entity_weight, 1.0) * 2.2
        graph_score = min(chunk_entity_counts.get(chunk_id, 0), 8) * 0.22
        judgment_score = 0.0
        if any(term in sentence for term in ("我认为", "我看", "大概", "总之", "终于", "仿佛", "注定", "不得不")):
            judgment_score += 0.9
        if "，" in sentence or "；" in sentence:
            judgment_score += 0.35
        position_score = 0.16 / max(chapter_index, 1) + 0.06 / max(paragraph_index, 1)
        return length_score * 2.0 + style_score * 1.35 + entity_score + graph_score + judgment_score + position_score - weak_penalty

    sentence_candidates: list[dict[str, str | float | int]] = []
    seen_sentences: set[str] = set()
    for chunk in book.chunks:
        for sentence in split_sentences(chunk.text or ""):
            sentence = clean_text(sentence)
            if sentence in seen_sentences:
                continue
            seen_sentences.add(sentence)
            score = sentence_score(sentence, chunk.chunk_id, chunk.chapter_index, chunk.paragraph_index)
            if score <= 0:
                continue
            sentence_candidates.append(
                {
                    "text": sentence,
                    "kind": "sentence",
                    "weight": round(score, 3),
                    "chapter": chunk.chapter_index,
                    "paragraph": chunk.paragraph_index,
                    "chunk_id": chunk.chunk_id,
                }
            )

    sentence_candidates.sort(key=lambda item: float(item["weight"]), reverse=True)
    selected_sentences: list[dict[str, str | float | int]] = []
    selected_chapters: set[int] = set()
    for item in sentence_candidates:
        chapter = int(item["chapter"])
        if len(selected_sentences) < max(3, sentence_limit // 2) or chapter not in selected_chapters:
            selected_sentences.append(item)
            selected_chapters.add(chapter)
        if len(selected_sentences) >= sentence_limit:
            break
    if len(selected_sentences) < sentence_limit:
        for item in sentence_candidates:
            if item not in selected_sentences:
                selected_sentences.append(item)
            if len(selected_sentences) >= sentence_limit:
                break

    phrase_counter: Counter[str] = Counter()
    book_text = "\n".join(chunk.text or "" for chunk in book.chunks)
    for phrase in style_terms:
        count = book_text.count(phrase)
        if count:
            phrase_counter[phrase] += 3 + count
    for phrase in semantic_phrase_terms:
        count = book_text.count(phrase)
        if count:
            phrase_counter[phrase] += 4 + count
    for entity in (graph.entities.values() if graph else []):
        if entity.entity_type not in {"location", "artifact", "theme", "concept"}:
            continue
        phrase = entity.canonical_name.strip()
        if not 2 <= len(phrase) <= 10:
            continue
        if phrase in weak_terms:
            continue
        phrase_counter[phrase] += 2 + min(entity.mention_count / max_entity_weight, 1.0) * 4

    selected_phrases: list[dict[str, str | float | int]] = []
    seen_phrases: set[str] = set()
    for phrase, count in phrase_counter.most_common(max(phrase_limit * 4, 1)):
        if phrase in seen_phrases or phrase in weak_terms:
            continue
        if any(phrase != other and phrase in other and count <= phrase_counter[other] for other in phrase_counter):
            continue
        if any(phrase in str(sentence["text"]) and len(phrase) > 10 for sentence in selected_sentences):
            continue
        seen_phrases.add(phrase)
        selected_phrases.append(
            {
                "text": phrase,
                "kind": "phrase",
                "weight": round(float(count), 3),
                "chapter": 0,
                "paragraph": 0,
                "chunk_id": "",
            }
        )
        if len(selected_phrases) >= phrase_limit:
            break

    lines = selected_phrases + selected_sentences
    lines.sort(key=lambda item: (str(item["kind"]) != "phrase", -float(item["weight"])))
    return {
        "book_id": book.book_id,
        "title": book.title,
        "source": "graph_centrality_text_candidates" if graph and graph.entities else "text_candidates",
        "lines": lines[:normalized_limit],
    }


@app.get("/api/books/{book_id}/graph/metadata")
def graph_metadata(book_id: str):
    try:
        return load_graph_metadata(book_id)
    except FileNotFoundError:
        graph = get_or_build_graph(book_id)
        return {
            "graph_id": graph.graph_id,
            "book_id": graph.book_id,
            "title": graph.title,
            "graph_version": graph.graph_version,
            "storage": graph.metadata.get("storage", {}),
            "stats": graph.stats().model_dump(),
        }


@app.get("/api/books/{book_id}/graph/audit")
def graph_audit(book_id: str, relation_id: str | None = None, entity_id: str | None = None):
    graph = get_or_build_graph(book_id)
    if relation_id:
        relation = graph.relations.get(relation_id)
        if relation is None:
            raise HTTPException(status_code=404, detail="relation_not_found")
        return {
            "relation": relation.model_dump(),
            "story_timeline": relation.story_timeline,
            "system_timeline": relation.system_timeline,
            "source_entity": graph.entities.get(relation.source_entity_id).model_dump()
            if relation.source_entity_id in graph.entities
            else None,
            "target_entity": graph.entities.get(relation.target_entity_id).model_dump()
            if relation.target_entity_id in graph.entities
            else None,
            "supersedes": [
                graph.relations[edge_id].model_dump()
                for edge_id in relation.supersedes_edge_ids
                if edge_id in graph.relations
            ],
            "provenance": [item.model_dump() for item in relation.provenance],
        }
    if entity_id:
        entity = graph.entities.get(entity_id)
        if entity is None:
            raise HTTPException(status_code=404, detail="entity_not_found")
        relation_history = [
            relation.model_dump()
            for relation in graph.relations.values()
            if relation.source_entity_id == entity_id or relation.target_entity_id == entity_id
        ]
        relation_history.sort(key=lambda item: (item["valid_at_chapter"], item["valid_at_paragraph"]))
        return {
            "entity": entity.model_dump(),
            "relation_history": relation_history,
            "episodes": [
                graph.episodes[episode_id].model_dump()
                for episode_id in entity.episode_ids[:20]
                if episode_id in graph.episodes
            ],
        }
    return {
        "graph_id": graph.graph_id,
        "audit_capabilities": [
            "relation_evidence_chain",
            "relation_invalidation_chain",
            "entity_relation_history",
            "episode_provenance_trace",
        ],
    }


@app.get("/api/books/{book_id}/graph/view")
def graph_view(book_id: str, chapter: int = 1, paragraph: int = 0, limit: int = 18, scope: str = "chapter",
               from_chapter: int = 0, min_weight: float = 0.0, max_edges: int = 0):
    graph = get_or_build_graph(book_id)
    normalized_scope = scope.lower().strip()
    if normalized_scope not in {"chapter", "book"}:
        raise HTTPException(status_code=400, detail="invalid_graph_scope")
    full_book_chapter = max((ch.chapter_index for ch in graph.chapters.values()), default=chapter)
    effective_chapter = full_book_chapter if normalized_scope == "book" else chapter
    max_paragraph = None if normalized_scope == "book" else (paragraph if paragraph and paragraph > 0 else None)
    min_chapter = from_chapter if from_chapter > 0 else 0
    chapter_key = f"chapter_{chapter:03d}"
    chapter_node = graph.chapters.get(chapter_key)
    if normalized_scope == "chapter" and chapter_node is None:
        raise HTTPException(status_code=404, detail="chapter_not_found")

    def _entity_is_visible(entity) -> bool:
        if entity.first_seen_chapter > effective_chapter:
            return False
        if min_chapter and entity.last_seen_chapter < min_chapter:
            return False
        if max_paragraph is None:
            return True
        return entity.first_seen_chapter < effective_chapter or entity.first_seen_paragraph <= max_paragraph

    if normalized_scope == "book":
        entity_pool = [entity for entity in graph.entities.values() if _entity_is_visible(entity)]
        relation_ids = [
            relation.edge_id
            for relation in graph.relations.values()
            if relation.is_visible(max_chapter=effective_chapter, max_paragraph=max_paragraph)
        ]
        community_candidates = []
    else:
        chapter_entity_ids = list(chapter_node.entity_ids) if chapter_node else []
        entity_pool = [
            graph.entities[entity_id]
            for entity_id in chapter_entity_ids
            if entity_id in graph.entities and _entity_is_visible(graph.entities[entity_id])
        ]
        relation_ids = list(chapter_node.relation_ids) if chapter_node else []
        community_candidates = []

    entity_pool.sort(key=lambda item: item.mention_count, reverse=True)
    selected_entities = entity_pool[: max(6, limit)]
    selected_entity_ids = {entity.entity_id for entity in selected_entities}

    relation_pool = []
    for relation_id in relation_ids:
        relation = graph.relations.get(relation_id)
        if relation is None:
            continue
        if not relation.is_visible(max_chapter=effective_chapter, max_paragraph=max_paragraph):
            continue
        if min_chapter and relation.valid_at_chapter < min_chapter:
            continue
        relation_pool.append(relation)
        selected_entity_ids.add(relation.source_entity_id)
        selected_entity_ids.add(relation.target_entity_id)

    if len(selected_entity_ids) > limit:
        selected_entity_ids = {
            entity.entity_id
            for entity in sorted(
                [graph.entities[entity_id] for entity_id in selected_entity_ids if entity_id in graph.entities],
                key=lambda item: item.mention_count,
                reverse=True,
            )[:limit]
        }
        relation_pool = [
            relation
            for relation in relation_pool
            if relation.source_entity_id in selected_entity_ids and relation.target_entity_id in selected_entity_ids
        ]

    nodes = []
    for entity_id in selected_entity_ids:
        entity = graph.entities.get(entity_id)
        if entity is None:
            continue
        nodes.append(
            {
                "id": entity.entity_id,
                "label": entity.canonical_name,
                "type": entity.entity_type,
                "mention_count": entity.mention_count,
                "first_seen_chapter": entity.first_seen_chapter,
                "first_seen_paragraph": entity.first_seen_paragraph,
                "summary": entity.summary,
            }
        )

    # Edge weight threshold
    if min_weight > 0.0:
        relation_pool = [r for r in relation_pool if r.weight >= min_weight]
    # Max edges limit
    if max_edges > 0 and len(relation_pool) > max_edges:
        relation_pool.sort(key=lambda r: r.weight, reverse=True)
        relation_pool = relation_pool[:max_edges]

    edges = []
    for relation in relation_pool:
        if relation.source_entity_id not in selected_entity_ids or relation.target_entity_id not in selected_entity_ids:
            continue
        edges.append(
            {
                "id": relation.edge_id,
                "source": relation.source_entity_id,
                "target": relation.target_entity_id,
                "label": relation.relation_type,
                "fact": relation.fact,
                "state_family": relation.state_family,
                "status": relation.status,
                "weight": relation.weight,
                "valid_at_chapter": relation.valid_at_chapter,
                "valid_at_paragraph": relation.valid_at_paragraph,
            }
        )

    community_items = []
    saga_items = []
    return {
        "graph_id": graph.graph_id,
        "book_id": graph.book_id,
        "title": graph.title,
        "scope": normalized_scope,
        "chapter_index": chapter if normalized_scope == "chapter" else effective_chapter,
        "paragraph_limit": max_paragraph,
        "chapter_title": chapter_node.title if normalized_scope == "chapter" and chapter_node else "Whole Book",
        "stats": {
            "node_count": len(nodes),
            "edge_count": len(edges),
            "community_count": len(community_items),
            "saga_count": len(saga_items),
        },
        "nodes": sorted(nodes, key=lambda item: item["mention_count"], reverse=True),
        "edges": sorted(edges, key=lambda item: item["weight"], reverse=True),
        "communities": community_items,
        "sagas": saga_items,
    }


@app.post("/api/books/{book_id}/graph/query")
def graph_query(book_id: str, query: GraphQuery):
    graph = get_or_build_graph(book_id)
    effective_query = query
    if not effective_query.query and not effective_query.node_types:
        effective_query = effective_query.model_copy(update={"node_types": ["chapter", "episode"]})
    result = TemporalGraphRetriever().retrieve(graph, effective_query)
    return result.model_dump()


@app.post("/api/books/{book_id}/memory/rebuild-jobs")
def create_memory_rebuild_job(book_id: str):
    try:
        get_or_build_book(book_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="book_not_found") from exc
    job = memory_job_registry.create(book_id)
    Thread(target=_process_memory_rebuild_job, args=(job.job_id, book_id), daemon=True).start()
    return job.to_dict()


@app.get("/api/books/{book_id}/memory/rebuild-jobs/{job_id}")
def memory_rebuild_job_status(book_id: str, job_id: str):
    job = memory_job_registry.get(job_id)
    if job is None or job.book_id != book_id:
        raise HTTPException(status_code=404, detail="memory_job_not_found")
    return job.to_dict()


@app.get("/api/books/{book_id}/memory/status")
def book_memory_status(book_id: str):
    try:
        book = get_or_build_book(book_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="book_not_found") from exc
    return memory_status(book)


@app.get("/api/books/{book_id}/memory/entities")
def book_memory_entities(book_id: str, type: str = "", chapter: int = 0, query: str = ""):
    try:
        book = get_or_build_book(book_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="book_not_found") from exc
    return list_entities(book, entity_type=type, chapter=chapter, query=query)


@app.get("/api/books/{book_id}/memory/entities/{entity_id}")
def book_memory_entity_detail(book_id: str, entity_id: str):
    try:
        book = get_or_build_book(book_id)
        return entity_detail(book, entity_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="book_not_found") from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="entity_not_found") from exc


@app.get("/api/books/{book_id}/memory/map")
def book_memory_map(
    book_id: str,
    scope: str = "passage",
    chapter: int = 1,
    paragraph: int = 0,
    entity_id: str = "",
    limit: int = 18,
):
    if scope not in {"passage", "chapter", "character"}:
        raise HTTPException(status_code=400, detail="invalid_memory_map_scope")
    try:
        book = get_or_build_book(book_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="book_not_found") from exc
    return memory_map(book, scope=scope, chapter=chapter, paragraph=paragraph, entity_id=entity_id, limit=limit)


@app.post("/api/books/{book_id}/bubbles/candidates")
def bubble_candidates(book_id: str, request: InlineBubbleRequest):
    try:
        book = get_or_build_book(book_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="book_not_found") from exc
    try:
        bubbles = generate_inline_bubbles(
            book,
            current_chapter=request.current_chapter,
            visible_chunk_ids=request.visible_chunk_ids,
            persona_id=request.persona_id,
            assistant_mode=request.assistant_mode,
            character_name=request.character_name,
            max_bubbles=request.max_bubbles,
        )
        return [item.model_dump() for item in bubbles]
    except (PersonaAgentConfigurationError, PersonaAgentInvocationError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/upload", response_model=UploadResponse)
async def upload_book(file: UploadFile = File(...)) -> UploadResponse:
    original_name = file.filename or "uploaded.txt"
    suffix = Path(original_name).suffix.lower() or ".txt"
    raw_bytes = await file.read()
    try:
        text = read_uploaded_text(original_name, raw_bytes)
    except UnsupportedUploadFormatError as exc:
        raise HTTPException(status_code=415, detail=str(exc)) from exc
    except UploadTextExtractionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    title = Path(original_name).stem
    safe_name = slugify(title)
    upload_path = UPLOADS_DIR / f"{safe_name}{suffix}"
    if suffix in SUPPORTED_UPLOAD_SUFFIXES - {".txt"}:
        upload_path.write_bytes(raw_bytes)
    else:
        upload_path.write_text(text, encoding="utf-8")

    record = build_book_record_from_upload(
        title=title,
        filename=original_name,
        raw_bytes=raw_bytes if suffix != ".txt" else text.encode("utf-8"),
        source_path=upload_path,
    )
    build_log = GraphBuildLogger(book_id=record.book_id, title=record.title)
    graph = TemporalGraphBuilder(
        strict_llm_extraction=True,
        build_logger=build_log,
    ).build(record)
    save_book(record)
    save_graph(graph)
    save_memory_index(build_memory_index(record, graph))
    return UploadResponse(
        book_id=record.book_id,
        title=record.title,
        chapter_count=record.chapter_count,
        chunk_count=len(record.chunks),
    )


@app.post("/api/upload-jobs")
async def create_upload_job(file: UploadFile = File(...)):
    original_name = file.filename or "uploaded.txt"
    suffix = Path(original_name).suffix.lower() or ".txt"
    if suffix not in SUPPORTED_UPLOAD_SUFFIXES:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported upload format '{suffix or 'unknown'}'. Supported formats: txt, pdf, epub.",
        )
    raw_bytes = await file.read()
    job = upload_job_registry.create()
    Thread(
        target=_process_upload_job,
        kwargs={"job_id": job.job_id, "original_name": original_name, "suffix": suffix, "raw_bytes": raw_bytes},
        daemon=True,
    ).start()
    return job.to_dict()


@app.get("/api/upload-jobs/{job_id}")
def upload_job_status(job_id: str):
    job = upload_job_registry.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="upload_job_not_found")
    return job.to_dict()


@app.post("/api/qa")
def ask_question(request: QuestionRequest):
    try:
        book = get_or_build_book(request.book_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="book_not_found") from exc
    try:
        return build_answer(request, book.chunks)
    except PersonaAgentConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except PersonaAgentInvocationError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/orchestrate")
def orchestrate(payload: dict):
    book_id = payload["book_id"]
    book = load_book(book_id)
    graph = get_or_build_graph(book_id)
    service = OrchestrationService()
    result = service.orchestrate(
        chunks=book.chunks,
        request_id=payload.get("request_id", f"orchestrate-{book_id}"),
        book_id=book_id,
        query=payload.get("query", ""),
        reading_progress=payload["reading_progress"],
        selection_context=payload.get("selection_context"),
        top_k=payload.get("top_k", 6),
        temporal_graph=graph,
        window_mode=payload.get("window_mode", "visible"),
    )
    return result.model_dump()


@app.post("/api/summary")
def chapter_summary(request: SummaryRequest):
    try:
        book = get_or_build_book(request.book_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="book_not_found") from exc
    try:
        return summarize_chapter(book, request.current_chapter, request.persona_id)
    except PersonaAgentConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except PersonaAgentInvocationError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="frontend")
