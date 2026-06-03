from __future__ import annotations

import re

from backend.api.schemas import AnswerCitation, AnswerClaim, QuestionRequest, QuestionResponse, RetrievedContext
from backend.knowledge_graph.storage import load_graph
from backend.knowledge_graph.orchestration.models import ReadingProgress, SelectionAnchor, SelectionContext
from backend.knowledge_graph.orchestration.service import OrchestrationService
from backend.agents.celebrity.persona_service import generate_persona_response
from backend.agents.celebrity.retrieval import retrieve_chunks
from backend.safety.anti_spoiler import is_spoiler_question


def _merge_contexts(local_hits: list[RetrievedContext], graph_hits) -> list[RetrievedContext]:
    merged: dict[str, RetrievedContext] = {hit.chunk_id: hit for hit in local_hits}
    for hit in graph_hits:
        paragraph_index = hit.paragraph_id if hit.paragraph_id is not None else 0
        merged.setdefault(
            hit.chunk_id,
            RetrievedContext(
                chunk_id=hit.chunk_id,
                chapter_index=hit.chapter_id,
                paragraph_index=paragraph_index,
                score=1.0,
                text=hit.text,
            ),
        )
    return sorted(
        merged.values(),
        key=lambda item: (item.score, -item.chapter_index, -item.paragraph_index),
        reverse=True,
    )


def _build_graph_knowledge_block(structured_context: dict | None) -> str:
    """Build a structured graph knowledge block from retrieval constructor output."""
    if not structured_context:
        return ""

    parts: list[str] = []
    visible_facts = structured_context.get("visible_facts", [])
    entities = structured_context.get("entities", [])

    if visible_facts:
        parts.append("【可见图谱事实】")
        for item in visible_facts[:10]:
            parts.append(
                f"- {item.get('source_name')} --[{item.get('relation_type')}]--> {item.get('target_name')}：{item.get('fact')}"
            )
    if entities:
        parts.append("【相关实体】")
        for item in entities[:8]:
            parts.append(
                f"- {item.get('canonical_name')}（{item.get('entity_type')}）：{item.get('summary') or 'no summary'}"
            )

    if parts:
        parts.insert(0, "【知识图谱结构化上下文】")
    return "\n".join(parts)


def _clip_text(text: str, limit: int = 180) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1] + "…"


def _build_citations(contexts: list[RetrievedContext], graph_hits) -> list[AnswerCitation]:
    citations: dict[str, AnswerCitation] = {}
    for context in contexts:
        citations[context.chunk_id] = AnswerCitation(
            chunk_id=context.chunk_id,
            chapter_index=context.chapter_index,
            paragraph_index=context.paragraph_index,
            source_type="book_text",
            quote=_clip_text(context.text),
            score=context.score,
        )
    for hit in graph_hits:
        if not hit.chunk_id:
            continue
        citations.setdefault(
            hit.chunk_id,
            AnswerCitation(
                chunk_id=hit.chunk_id,
                chapter_index=hit.chapter_id,
                paragraph_index=hit.paragraph_id or 0,
                source_type="graph",
                quote=_clip_text(hit.text),
                score=hit.score,
            ),
        )
    return list(citations.values())[:10]


def _claim_units(text: str) -> set[str]:
    lowered = text.lower()
    units = {
        token
        for token in re.findall(r"[a-z0-9_]{2,}", lowered)
        if token not in {"the", "and", "this", "that", "with", "from", "current"}
    }
    cjk_chars = re.findall(r"[\u4e00-\u9fff]", lowered)
    units.update("".join(cjk_chars[index : index + 2]) for index in range(max(0, len(cjk_chars) - 1)))
    units.update("".join(cjk_chars[index : index + 3]) for index in range(max(0, len(cjk_chars) - 2)))
    return {
        unit
        for unit in units
        if unit
        and unit not in {"这个", "那个", "因为", "所以", "但是", "他们", "我们", "自己", "当前", "文本", "问题"}
    }


def _looks_like_heading(sentence: str) -> bool:
    cleaned = sentence.strip().strip("*#-—:： ")
    if not cleaned:
        return True
    if len(cleaned) <= 16 and any(marker in sentence for marker in {"**", "：", ":"}):
        return True
    return bool(re.match(r"^\s*(\d+[\.\、]|[-*])\s*.{0,18}$", sentence))


def _extract_claims(answer: str, citations: list[AnswerCitation]) -> tuple[list[AnswerClaim], int, float]:
    evidence_text = "\n".join(citation.quote for citation in citations)
    evidence_tokens = _claim_units(evidence_text)
    sentences = [
        item.strip()
        for item in re.split(r"(?<=[。！？!?])\s*|\n+", answer)
        if item.strip()
    ]
    claims: list[AnswerClaim] = []
    unsupported = 0
    for sentence in sentences[:8]:
        if _looks_like_heading(sentence):
            continue
        tokens = _claim_units(sentence)
        matched = tokens.intersection(evidence_tokens)
        supported = len(matched) >= 2 or "证据不足" in sentence or "无法判断" in sentence or "不足以判断" in sentence
        if not supported:
            unsupported += 1
        claims.append(
            AnswerClaim(
                text=sentence,
                supported=supported,
                citation_chunk_ids=[citation.chunk_id for citation in citations[:3]] if supported else [],
            )
        )
    if not claims:
        return [], 0, 0.0
    confidence = max(0.0, min(1.0, 1.0 - unsupported / len(claims)))
    if citations and any(claim.supported for claim in claims):
        confidence = max(confidence, 0.55)
    return claims, unsupported, round(confidence, 3)


def _verified_answer(answer: str, claims: list[AnswerClaim], unsupported: int) -> str:
    if unsupported == 0:
        return answer
    supported_count = len([claim for claim in claims if claim.supported])
    if supported_count:
        if unsupported > supported_count:
            return f"{answer}\n\n（其中有 {unsupported} 条概括属于解释性判断，建议展开证据一起看。）"
        return f"{answer}\n\n（其中有 {unsupported} 条概括需要继续用后文或更多证据确认。）"
    supported_parts = [claim.text for claim in claims if claim.supported]
    if supported_parts:
        return "\n".join(supported_parts + ["（我已省略当前证据不足的推断。）"])
    return "当前已读内容不足以可靠回答这个问题。可以换一个更贴近当前段落的问题。"


def build_answer(request: QuestionRequest, chunks) -> QuestionResponse:
    safety = is_spoiler_question(request.question)
    try:
        graph = load_graph(request.book_id)
    except FileNotFoundError:
        graph = None

    orchestration = OrchestrationService().orchestrate(
        chunks=chunks,
        request_id=f"qa-{request.book_id}-{request.current_chapter}",
        book_id=request.book_id,
        query=request.question,
        reading_progress=ReadingProgress(
            book_id=request.book_id,
            chapter_id=request.current_chapter,
            paragraph_id=9999,
            token_offset=10**9,
        ),
        selection_context=SelectionContext(
            book_id=request.book_id,
            selected_text=request.highlight_text,
            anchor=SelectionAnchor(chapter_id=request.current_chapter, paragraph_id=0),
        ),
        top_k=request.top_k,
        temporal_graph=graph,
        window_mode="visible",
    )
    local_contexts = retrieve_chunks(
        chunks=chunks,
        query=f"{request.highlight_text} {request.question}".strip(),
        max_chapter=request.current_chapter,
        top_k=request.top_k,
    )
    contexts = _merge_contexts(local_contexts, orchestration.hits)[: request.top_k]
    visible_context_texts = [context.text for context in contexts]

    graph_knowledge = _build_graph_knowledge_block(orchestration.structured_context)
    if graph_knowledge:
        visible_context_texts.insert(0, graph_knowledge)

    if not safety.safe:
        refusal, model_name, _ = generate_persona_response(
            persona_id=request.persona_id,
            task="qa",
            book_title=request.book_id,
            question=(
                "用户的问题超出了已读范围，请拒绝剧透，并把话题收回到当前已读内容。\n"
                f"原问题：{request.question}"
            ),
            visible_contexts=visible_context_texts,
            current_chapter=request.current_chapter,
            highlight_text=request.highlight_text,
            top_k=request.top_k,
            conversation_history=request.conversation_history,
        )
        return QuestionResponse(
            answer=refusal,
            persona_id=request.persona_id,
            safe=False,
            reason=safety.reason,
            contexts=contexts,
            model_name=model_name,
            citations=_build_citations(contexts, orchestration.hits),
            claims=[
                AnswerClaim(
                    text="用户问题涉及未来剧情，回答被防剧透策略拦截。",
                    supported=True,
                    citation_chunk_ids=[],
                )
            ],
            confidence=1.0,
            unsupported_claim_count=0,
            retrieval_trace_id=orchestration.request_id,
        )

    if not visible_context_texts:
        answer, model_name, _ = generate_persona_response(
            persona_id=request.persona_id,
            task="qa",
            book_title=request.book_id,
            question=(
                "当前没有检索到足够正文证据。请用中文明确说明证据不足，"
                "并引导用户改问更贴近当前段落的问题。"
            ),
            visible_contexts=[],
            current_chapter=request.current_chapter,
            highlight_text=request.highlight_text,
            top_k=request.top_k,
            conversation_history=request.conversation_history,
        )
        return QuestionResponse(
            answer=answer,
            persona_id=request.persona_id,
            safe=True,
            reason="no_visible_context",
            contexts=[],
            model_name=model_name,
            citations=[],
            claims=[
                AnswerClaim(
                    text="当前没有检索到足够正文证据。",
                    supported=True,
                    citation_chunk_ids=[],
                )
            ],
            confidence=0.0,
            unsupported_claim_count=0,
            retrieval_trace_id=orchestration.request_id,
        )

    answer, model_name, _ = generate_persona_response(
        persona_id=request.persona_id,
        task="qa",
        book_title=request.book_id,
        question=request.question,
        visible_contexts=visible_context_texts,
        current_chapter=request.current_chapter,
        highlight_text=request.highlight_text,
        top_k=request.top_k,
        conversation_history=request.conversation_history,
    )
    citations = _build_citations(contexts, orchestration.hits)
    claims, unsupported, confidence = _extract_claims(answer, citations)
    answer = _verified_answer(answer, claims, unsupported)
    return QuestionResponse(
        answer=answer,
        persona_id=request.persona_id,
        safe=True,
        reason=safety.reason,
        contexts=contexts,
        model_name=model_name,
        citations=citations,
        claims=claims,
        confidence=confidence,
        unsupported_claim_count=unsupported,
        retrieval_trace_id=orchestration.request_id,
    )
