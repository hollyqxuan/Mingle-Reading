from __future__ import annotations

from backend.api.schemas import SummaryResponse
from backend.agents.celebrity.persona_service import generate_persona_response
from backend.knowledge_graph.orchestration.models import ReadingProgress
from backend.knowledge_graph.orchestration.service import OrchestrationService
from backend.knowledge_graph.storage import load_graph


def _build_summary_graph_context(structured_context: dict | None) -> list[str]:
    if not structured_context:
        return []

    blocks: list[str] = []
    for item in structured_context.get("visible_facts", [])[:8]:
        blocks.append(
            f"事实：{item.get('source_name')} --[{item.get('relation_type')}]--> {item.get('target_name')}：{item.get('fact')}"
        )
    for item in structured_context.get("local_communities", [])[:3]:
        blocks.append(f"关系团簇：{item.get('label')}：{item.get('summary')}")
    for item in structured_context.get("long_arcs", [])[:3]:
        blocks.append(
            f"叙事弧：{item.get('label')}（ch{item.get('chapter_start')}-{item.get('chapter_end')}）：{item.get('summary')}"
        )
    return blocks


def summarize_chapter(book, current_chapter: int, persona_id: str) -> SummaryResponse:
    chapter_chunks = [chunk for chunk in book.chunks if chunk.chapter_index == current_chapter]
    visible_contexts = [chunk.text.strip() for chunk in chapter_chunks if chunk.text.strip()]

    try:
        graph = load_graph(book.book_id)
    except FileNotFoundError:
        graph = None

    structured_context: dict | None = None
    if graph is not None:
        orchestration = OrchestrationService().orchestrate(
            chunks=book.chunks,
            request_id=f"summary-{book.book_id}-{current_chapter}",
            book_id=book.book_id,
            query=f"总结第 {current_chapter} 章目前已读范围的核心事件、人物关系与叙事推进",
            reading_progress=ReadingProgress(
                book_id=book.book_id,
                chapter_id=current_chapter,
                paragraph_id=9999,
                token_offset=10**9,
            ),
            selection_context=None,
            top_k=8,
            temporal_graph=graph,
            window_mode="recent",
        )
        structured_context = orchestration.structured_context

    summary_inputs = visible_contexts[:8] + _build_summary_graph_context(structured_context)
    summary, model_name, _ = generate_persona_response(
        persona_id=persona_id,
        task="summary",
        book_title=book.title,
        question=f"请总结这本书第 {current_chapter} 章目前已读范围的内容。",
        visible_contexts=summary_inputs[:12],
        current_chapter=current_chapter,
        top_k=5,
    )
    return SummaryResponse(
        summary=summary,
        chapter_id=f"chapter-{current_chapter:03d}",
        persona_id=persona_id,
        model_name=model_name,
    )
