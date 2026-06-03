from __future__ import annotations

from typing import Any

from backend.api.schemas import BookChunk
from backend.knowledge_graph.models import GraphQuery, TemporalContextGraph
from backend.knowledge_graph.retrieval import TemporalGraphRetriever

from .models import (
    Citation,
    EntityNetworkResult,
    GuardrailTrace,
    OrchestrationResult,
    ReadingProgress,
    RetrievalFilters,
    RetrievalHit,
    RetrievalRequest,
    SelectionContext,
)
from .utils import keyword_score, unique_preserve_order


class OrchestrationService:
    def orchestrate(
        self,
        *,
        chunks: list[BookChunk],
        request_id: str,
        book_id: str,
        query: str,
        reading_progress: ReadingProgress | dict[str, Any],
        selection_context: SelectionContext | dict[str, Any] | None = None,
        top_k: int = 6,
        temporal_graph: TemporalContextGraph | None = None,
        window_mode: str = "visible",
    ) -> OrchestrationResult:
        progress = self._coerce_progress(reading_progress, book_id)
        selection = self._coerce_selection(selection_context, book_id)
        filters = self._build_filters(progress, selection, chunks)
        retrieval_request = RetrievalRequest(
            request_id=request_id,
            scope="mixed",
            window_mode=window_mode,  # type: ignore[arg-type]
            kb_id=f"{book_id}-mixed",
            query=self._compose_query(query, selection),
            selection_context=selection,
            reading_progress=progress,
            filters=filters,
            top_k=top_k,
        )
        visible_chunks, trace = self._filter_visible_chunks(chunks, progress, selection, filters)
        text_hits = self._retrieve_text_hits(visible_chunks, retrieval_request)
        graph_hits, graph_trace, structured_graph_context = self._retrieve_graph_hits(
            visible_chunks, retrieval_request, temporal_graph
        )
        merged_hits = self._merge_hits(text_hits, graph_hits, top_k)
        trace.retrieval_counts = {
            "book_text": len(text_hits),
            "graph": len(graph_hits),
            "merged": len(merged_hits),
        }
        citations = [
            Citation(
                chunk_id=hit.chunk_id,
                source_type=hit.source_type,
                chapter_id=hit.chapter_id,
                section_id=hit.section_id,
                paragraph_id=hit.paragraph_id,
                score=hit.score,
            )
            for hit in merged_hits
        ]
        return OrchestrationResult(
            request_id=request_id,
            retrieval_request=retrieval_request,
            hits=merged_hits,
            citations=citations,
            guardrail_trace=trace,
            retrieval_trace=graph_trace,
            structured_context=structured_graph_context,
        )

    def retrieve_entity_network(
        self,
        graph: TemporalContextGraph,
        *,
        entity_name: str | None = None,
        entity_id: str | None = None,
        query: str | None = None,
        max_chapter: int | None = None,
    ) -> EntityNetworkResult | None:
        """Retrieve the complete ego-network for a single entity.

        Unlike orchestrate() which ranks/re-ranks across all nodes, this pulls
        ALL relations, neighbours, communities, and sagas for one entity.
        The query is only used to sort relations within the result, not to filter.
        """
        from backend.knowledge_graph.retrieval import _text_score, _tokenize

        # --- 1. Find the entity ---
        target_entity = None
        if entity_id:
            target_entity = graph.entities.get(entity_id)
        if target_entity is None and entity_name:
            candidates = []
            for e in graph.entities.values():
                if e.entity_type != "character":
                    continue
                if entity_name in e.canonical_name:
                    candidates.append(e)
            if candidates:
                target_entity = max(candidates, key=lambda e: e.mention_count)
        if target_entity is None:
            return None

        eid = target_entity.entity_id

        # --- 2. All relations involving this entity ---
        query_tokens = _tokenize(query) if query else []
        relations: list[dict[str, Any]] = []
        for r in graph.relations.values():
            if r.source_entity_id != eid and r.target_entity_id != eid:
                continue
            if max_chapter is not None and r.valid_at_chapter > max_chapter:
                continue
            src = graph.entities.get(r.source_entity_id)
            tgt = graph.entities.get(r.target_entity_id)
            if src is None or tgt is None:
                continue
            item = {
                "source_name": src.canonical_name,
                "target_name": tgt.canonical_name,
                "relation_type": r.relation_type,
                "state_family": r.state_family,
                "fact": r.fact,
                "status": r.status,
                "valid_at_chapter": r.valid_at_chapter,
                "weight": r.weight,
            }
            # Include source text excerpt for evidence
            if r.episode_ids:
                first_ep = graph.episodes.get(r.episode_ids[0])
                if first_ep and first_ep.text:
                    item["source_text"] = first_ep.text[:300]
                    item["source_chapter"] = first_ep.chapter_index
            # Score for ordering (query only affects rank, not inclusion)
            if query_tokens:
                item["_score"] = _text_score(
                    query_tokens,
                    f"{src.canonical_name} {tgt.canonical_name} {r.relation_type} {r.fact}",
                )
            else:
                item["_score"] = 0.0
            relations.append(item)

        # Sort: FAMILY_OF first, then by query score, then by chapter
        type_priority = {"FAMILY_OF": 0, "CARES_ABOUT": 1, "SPOKE_WITH": 2, "CONFLICTS_WITH": 3,
                         "LOCATED_IN": 4, "ACCOMPANIES": 5, "MEMBER_OF": 6, "OWNS": 7}
        relations.sort(key=lambda r: (
            type_priority.get(r["relation_type"], 9),
            -(r.get("_score", 0)),
            r["valid_at_chapter"],
        ))

        # --- 3. Neighbour entities ---
        neighbour_ids: set[str] = set()
        for r in graph.relations.values():
            if r.source_entity_id == eid:
                neighbour_ids.add(r.target_entity_id)
            elif r.target_entity_id == eid:
                neighbour_ids.add(r.source_entity_id)

        neighbours: list[dict[str, Any]] = []
        for nid in neighbour_ids:
            ne = graph.entities.get(nid)
            if ne is None:
                continue
            if max_chapter is not None and ne.first_seen_chapter > max_chapter:
                continue
            neighbours.append({
                "entity_id": ne.entity_id,
                "canonical_name": ne.canonical_name,
                "entity_type": ne.entity_type,
                "mention_count": ne.mention_count,
                "first_seen_chapter": ne.first_seen_chapter,
                "summary": ne.summary or "",
            })
        neighbours.sort(key=lambda n: -n["mention_count"])

        # --- 4. Communities containing this entity ---
        communities: list[dict[str, Any]] = []
        for c in graph.communities.values():
            if eid in c.entity_ids:
                if max_chapter is not None and c.chapter_start > max_chapter:
                    continue
                communities.append({
                    "label": c.label,
                    "summary": c.summary,
                    "chapter_start": c.chapter_start,
                    "chapter_end": c.chapter_end,
                    "entity_count": len(c.entity_ids),
                })

        # --- 5. Sagas mentioning this entity ---
        sagas: list[dict[str, Any]] = []
        for s in graph.sagas.values():
            if eid in s.key_entities:
                if max_chapter is not None and s.chapter_start > max_chapter:
                    continue
                sagas.append({
                    "label": s.label,
                    "summary": s.summary,
                    "chapter_start": s.chapter_start,
                    "chapter_end": s.chapter_end,
                })

        return EntityNetworkResult(
            entity_id=eid,
            canonical_name=target_entity.canonical_name,
            entity_type=target_entity.entity_type,
            mention_count=target_entity.mention_count,
            first_seen_chapter=target_entity.first_seen_chapter,
            last_seen_chapter=target_entity.last_seen_chapter,
            summary=target_entity.summary or "",
            aliases=list(target_entity.aliases),
            relations=relations,
            neighbour_entities=neighbours,
            communities=communities,
            sagas=sagas,
        )

    def _coerce_progress(
        self,
        reading_progress: ReadingProgress | dict[str, Any],
        book_id: str,
    ) -> ReadingProgress:
        progress = (
            reading_progress
            if isinstance(reading_progress, ReadingProgress)
            else ReadingProgress.model_validate(reading_progress)
        )
        if progress.book_id != book_id:
            progress = progress.model_copy(update={"book_id": book_id})
        return progress

    def _coerce_selection(
        self,
        selection_context: SelectionContext | dict[str, Any] | None,
        book_id: str,
    ) -> SelectionContext | None:
        if selection_context is None:
            return None
        selection = (
            selection_context
            if isinstance(selection_context, SelectionContext)
            else SelectionContext.model_validate(selection_context)
        )
        if selection.book_id != book_id:
            selection = selection.model_copy(update={"book_id": book_id})
        return selection

    def _compose_query(self, query: str, selection: SelectionContext | None) -> str:
        parts = [query.strip()]
        if selection and selection.selected_text.strip():
            parts.append(selection.selected_text.strip())
        return " ".join(part for part in parts if part)

    def _build_filters(
        self,
        progress: ReadingProgress,
        selection: SelectionContext | None,
        chunks: list[BookChunk],
    ) -> RetrievalFilters:
        character_ids = unique_preserve_order(self._collect_character_filters(selection, chunks, progress))
        theme_tags = unique_preserve_order(self._collect_theme_filters(selection, chunks, progress))
        return RetrievalFilters(
            max_chapter_id=progress.chapter_id,
            max_paragraph_id=progress.paragraph_id,
            max_token_offset=progress.token_offset,
            character_ids=character_ids,
            theme_tags=theme_tags,
        )

    def _collect_character_filters(
        self,
        selection: SelectionContext | None,
        chunks: list[BookChunk],
        progress: ReadingProgress,
    ) -> list[str]:
        if not selection:
            return []
        selected = selection.selected_text.lower()
        visible = [chunk for chunk in chunks if self._is_chunk_visible(chunk, progress)]
        candidates: list[str] = []
        for chunk in visible:
            chunk_characters = self._chunk_characters(chunk)
            if selection.anchor and self._chunk_paragraph_id(chunk) == selection.anchor.paragraph_id:
                candidates.extend(chunk_characters)
                continue
            for character in chunk_characters:
                if character.replace("_", " ") in selected:
                    candidates.append(character)
        return candidates

    def _collect_theme_filters(
        self,
        selection: SelectionContext | None,
        chunks: list[BookChunk],
        progress: ReadingProgress,
    ) -> list[str]:
        if not selection:
            return []
        visible = [chunk for chunk in chunks if self._is_chunk_visible(chunk, progress)]
        anchor_paragraph = selection.anchor.paragraph_id if selection.anchor else None
        themes: list[str] = []
        for chunk in visible:
            if anchor_paragraph is not None and self._chunk_paragraph_id(chunk) != anchor_paragraph:
                continue
            themes.extend(self._chunk_theme_tags(chunk))
        return themes

    def _filter_visible_chunks(
        self,
        chunks: list[BookChunk],
        progress: ReadingProgress,
        selection: SelectionContext | None,
        filters: RetrievalFilters,
    ) -> tuple[list[BookChunk], GuardrailTrace]:
        visible = [chunk for chunk in chunks if self._is_chunk_visible(chunk, progress)]
        progress_consistent = True
        warnings: list[str] = []
        if selection and selection.anchor and selection.anchor.chapter_id > progress.chapter_id:
            progress_consistent = False
            warnings.append("selection_anchor_after_progress")
        trace = GuardrailTrace(
            max_chapter_id=filters.max_chapter_id,
            max_paragraph_id=filters.max_paragraph_id,
            max_token_offset=filters.max_token_offset,
            visible_chunk_count=len(visible),
            filtered_out_chunk_count=max(0, len(chunks) - len(visible)),
            progress_consistent=progress_consistent,
            selection_anchor_chapter_id=selection.anchor.chapter_id if selection and selection.anchor else None,
            selected_character_filters=filters.character_ids,
            selected_theme_filters=filters.theme_tags,
            retrieval_plan=[
                "apply_progress_filters_before_retrieval",
                "retrieve_book_text_hits",
                "retrieve_graph_hits",
                "merge_and_deduplicate_hits",
            ],
            warnings=warnings,
        )
        return visible, trace

    def _retrieve_text_hits(
        self,
        visible_chunks: list[BookChunk],
        retrieval_request: RetrievalRequest,
    ) -> list[RetrievalHit]:
        scored: list[tuple[float, BookChunk]] = []
        for chunk in visible_chunks:
            score = keyword_score(retrieval_request.query, chunk.text)
            score += self._progress_bonus(chunk, retrieval_request.reading_progress, retrieval_request.selection_context)
            if score <= 0:
                continue
            scored.append((score, chunk))
        ranked = sorted(scored, key=lambda item: item[0], reverse=True)
        return [
            RetrievalHit(
                chunk_id=chunk.chunk_id,
                source_type="book_text",
                score=round(score, 4),
                text=chunk.text,
                chapter_id=chunk.chapter_index,
                section_id=self._safe_int(chunk.section_id),
                paragraph_id=self._chunk_paragraph_id(chunk),
                metadata=self._chunk_metadata(chunk),
            )
            for score, chunk in ranked[: retrieval_request.top_k]
        ]

    def _retrieve_graph_hits(
        self,
        visible_chunks: list[BookChunk],
        retrieval_request: RetrievalRequest,
        temporal_graph: TemporalContextGraph | None = None,
    ) -> tuple[list[RetrievalHit], dict[str, Any], dict[str, Any]]:
        if temporal_graph is not None:
            graph_result = TemporalGraphRetriever().retrieve(
                temporal_graph,
                GraphQuery(
                    query=retrieval_request.query,
                    window_mode=retrieval_request.window_mode,
                    max_chapter=retrieval_request.reading_progress.chapter_id,
                    max_paragraph=retrieval_request.reading_progress.paragraph_id,
                    top_k=retrieval_request.top_k,
                ),
            )
            hits: list[RetrievalHit] = []
            for hit in graph_result.hits:
                payload = dict(hit.payload)
                provenance = hit.provenance[0] if hit.provenance else None
                chunk_id = payload.get("chunk_id") or (provenance.chunk_id if provenance else hit.hit_id)
                paragraph_id = payload.get("paragraph_id") or (provenance.paragraph_index if provenance else None)
                section_id = payload.get("section_id")
                if section_id is None and provenance is not None:
                    section_id = provenance.metadata.get("section_id")
                hits.append(
                    RetrievalHit(
                        chunk_id=chunk_id,
                        source_type="graph",
                        score=round(hit.score, 4),
                        text=payload.get("text") or payload.get("summary") or payload.get("label") or hit.reason,
                        chapter_id=hit.chapter_index or (provenance.chapter_index if provenance else 0),
                        section_id=self._safe_int(section_id),
                        paragraph_id=paragraph_id,
                        metadata=payload,
                    )
                )
            return hits, graph_result.retrieval_trace, graph_result.structured_context

        character_filters = set(retrieval_request.filters.character_ids)
        theme_filters = set(retrieval_request.filters.theme_tags)
        scored: list[tuple[float, BookChunk]] = []
        for chunk in visible_chunks:
            score = 0.0
            chunk_characters = set(self._chunk_characters(chunk))
            chunk_themes = set(self._chunk_theme_tags(chunk))
            if character_filters and chunk_characters:
                score += 1.2 * len(character_filters & chunk_characters)
            if theme_filters and chunk_themes:
                score += 0.8 * len(theme_filters & chunk_themes)
            if not character_filters and not theme_filters:
                score += self._progress_bonus(
                    chunk,
                    retrieval_request.reading_progress,
                    retrieval_request.selection_context,
                )
            else:
                score += self._progress_bonus(
                    chunk,
                    retrieval_request.reading_progress,
                    retrieval_request.selection_context,
                ) * 0.5
            if score <= 0:
                continue
            scored.append((score, chunk))
        ranked = sorted(scored, key=lambda item: item[0], reverse=True)
        hits: list[RetrievalHit] = []
        for score, chunk in ranked[: retrieval_request.top_k]:
            hit_text = self._graph_summary_text(chunk)
            hits.append(
                RetrievalHit(
                    chunk_id=chunk.chunk_id,
                    source_type="graph",
                    score=round(score, 4),
                    text=hit_text,
                    chapter_id=chunk.chapter_index,
                    section_id=self._safe_int(chunk.section_id),
                    paragraph_id=self._chunk_paragraph_id(chunk),
                    metadata=self._chunk_metadata(chunk),
                )
            )
        return hits, {"fallback": True, "search_counts": {"graph": len(hits)}}, {"visible_facts": [], "entities": [], "local_communities": [], "long_arcs": [], "citations": []}

    def _merge_hits(
        self,
        text_hits: list[RetrievalHit],
        graph_hits: list[RetrievalHit],
        top_k: int,
    ) -> list[RetrievalHit]:
        merged_by_source_id: dict[tuple[str, str], RetrievalHit] = {}
        for hit in text_hits + graph_hits:
            merged_by_source_id[(hit.chunk_id, hit.source_type)] = hit
        ranked = sorted(
            merged_by_source_id.values(),
            key=lambda hit: (hit.score, hit.chapter_id, hit.paragraph_id or 0),
            reverse=True,
        )
        return ranked[:top_k]

    def _is_chunk_visible(self, chunk: BookChunk, progress: ReadingProgress) -> bool:
        if chunk.chapter_index < progress.chapter_id:
            return True
        if chunk.chapter_index > progress.chapter_id:
            return False
        paragraph_id = self._chunk_paragraph_id(chunk)
        if progress.paragraph_id is not None and paragraph_id is not None and paragraph_id > progress.paragraph_id:
            return False
        if (
            progress.token_offset is not None
            and chunk.token_offset
            and chunk.token_offset > progress.token_offset
            and (progress.paragraph_id is None or paragraph_id == progress.paragraph_id)
        ):
            return False
        return True

    def _progress_bonus(
        self,
        chunk: BookChunk,
        progress: ReadingProgress,
        selection: SelectionContext | None,
    ) -> float:
        bonus = 0.0
        if chunk.chapter_index == progress.chapter_id:
            bonus += 0.3
        paragraph_id = self._chunk_paragraph_id(chunk)
        if selection and selection.anchor and paragraph_id is not None and selection.anchor.paragraph_id is not None:
            distance = abs(paragraph_id - selection.anchor.paragraph_id)
            bonus += max(0.0, 0.6 - 0.15 * distance)
        return bonus

    def _chunk_characters(self, chunk: BookChunk) -> list[str]:
        values = chunk.metadata.get("characters_present") or chunk.candidate_characters
        return unique_preserve_order(values)

    def _chunk_theme_tags(self, chunk: BookChunk) -> list[str]:
        values = chunk.metadata.get("theme_tags") or chunk.tags
        return unique_preserve_order(values)

    def _chunk_metadata(self, chunk: BookChunk) -> dict[str, Any]:
        metadata = dict(chunk.metadata)
        metadata.setdefault("book_id", chunk.book_id)
        metadata.setdefault("chapter_id", chunk.chapter_index)
        metadata.setdefault("section_id", self._safe_int(chunk.section_id))
        metadata.setdefault("paragraph_id", self._chunk_paragraph_id(chunk))
        metadata.setdefault("characters_present", self._chunk_characters_fallback(chunk))
        metadata.setdefault("theme_tags", unique_preserve_order(chunk.tags))
        metadata.setdefault("spoiler_level", chunk.spoiler_guard.get("spoiler_level", "visible"))
        return metadata

    def _chunk_characters_fallback(self, chunk: BookChunk) -> list[str]:
        return unique_preserve_order(chunk.candidate_characters)

    def _graph_summary_text(self, chunk: BookChunk) -> str:
        characters = ", ".join(self._chunk_characters(chunk)) or "unknown_characters"
        themes = ", ".join(self._chunk_theme_tags(chunk)) or "untagged_theme"
        return f"graph view: chapter {chunk.chapter_index}, characters={characters}, themes={themes}"

    def _chunk_paragraph_id(self, chunk: BookChunk) -> int | None:
        return self._safe_int(chunk.paragraph_id)

    def _safe_int(self, value: Any) -> int | None:
        if value is None:
            return None
        if isinstance(value, int):
            return value
        digits = "".join(character for character in str(value) if character.isdigit())
        if not digits:
            return None
        return int(digits)
