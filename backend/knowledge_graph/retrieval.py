from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any

from .models import GraphHit, GraphQuery, GraphRetrievalResult, TemporalContextGraph


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[\w\u4e00-\u9fff]+", text.lower())


def _text_score(query_tokens: list[str], text: str) -> float:
    if not query_tokens:
        return 0.0
    text_tokens = _tokenize(text)
    if not text_tokens:
        return 0.0
    overlap = sum(1 for token in query_tokens if token in text_tokens)
    return overlap / math.sqrt(len(text_tokens))


def _matches_metadata(metadata: dict[str, Any], filters: dict[str, Any]) -> bool:
    for key, expected in filters.items():
        if metadata.get(key) != expected:
            return False
    return True


class TemporalGraphRetriever:
    """Retrieve progress-aware temporal context with graph filters and browse support."""

    def retrieve(self, graph: TemporalContextGraph, query: GraphQuery) -> GraphRetrievalResult:
        visible_episode_ids, visible_entity_ids = self._resolve_visibility(graph, query)
        query_tokens = _tokenize(query.query)
        requested_entity_terms = {item.strip().lower() for item in query.entity_names if item.strip()}
        requested_tags = set(query.tags)
        requested_node_types = set(query.node_types)

        search_results: dict[str, list[GraphHit]] = {
            "episode": [],
            "chapter": [],
            "entity": [],
            "relation": [],
        }
        if not requested_node_types or "episode" in requested_node_types:
            search_results["episode"] = self._retrieve_episodes(
                graph=graph,
                query=query,
                query_tokens=query_tokens,
                visible_episode_ids=visible_episode_ids,
                requested_entity_terms=requested_entity_terms,
                requested_tags=requested_tags,
            )
        ranked_episode_hits = sorted(search_results["episode"], key=lambda item: item.score, reverse=True)[: query.top_k]
        supporting_episode_ids = {hit.hit_id for hit in ranked_episode_hits if hit.hit_type == "episode"}

        if query.include_chapters and (not requested_node_types or "chapter" in requested_node_types):
            search_results["chapter"] = self._retrieve_chapters(graph, query, supporting_episode_ids, query_tokens)
        if query.include_entities and (not requested_node_types or "entity" in requested_node_types):
            search_results["entity"] = self._retrieve_entities(
                graph, query, visible_entity_ids, supporting_episode_ids, query_tokens
            )
        if query.include_relations and (not requested_node_types or "relation" in requested_node_types):
            search_results["relation"] = self._retrieve_relations(
                graph,
                query,
                visible_episode_ids,
                visible_entity_ids,
                supporting_episode_ids,
                query_tokens,
            )
        if query.include_communities and (not requested_node_types or "community" in requested_node_types):
            pass  # community layer removed — no-op
        if query.include_sagas and (not requested_node_types or "saga" in requested_node_types):
            pass  # saga layer removed — no-op

        expansion_hits = self._graph_expand_hits(
            graph=graph,
            query=query,
            visible_episode_ids=visible_episode_ids,
            visible_entity_ids=visible_entity_ids,
            seed_episode_hits=ranked_episode_hits,
            seed_entity_hits=search_results["entity"][: max(2, query.top_k // 2)],
        )
        for hit in expansion_hits:
            search_results.setdefault(hit.hit_type, []).append(hit)

        reranked_hits = self._rerank_hits(search_results, query)
        structured_context = self._construct_context(graph, reranked_hits)
        hit_breakdown = Counter(hit.hit_type for hit in reranked_hits)
        search_counts = {key: len(value) for key, value in search_results.items()}
        return GraphRetrievalResult(
            query=query,
            hits=reranked_hits,
            visible_episode_count=len(visible_episode_ids),
            visible_entity_count=len(visible_entity_ids),
            applied_filters={
                "window_mode": query.window_mode,
                "max_chapter": query.max_chapter,
                "max_paragraph": query.max_paragraph,
                "min_chapter": query.min_chapter,
                "min_paragraph": query.min_paragraph,
                "recent_episode_count": query.recent_episode_count,
                "entity_names": query.entity_names,
                "entity_types": query.entity_types,
                "relation_types": query.relation_types,
                "state_families": query.state_families,
                "relation_statuses": query.relation_statuses,
                "tags": query.tags,
                "node_types": query.node_types,
                "metadata_filters": query.metadata_filters,
            },
            hit_type_breakdown=dict(hit_breakdown),
            graph_metadata=graph.metadata,
            graph_stats=graph.stats(),
            retrieval_trace={
                "search_counts": search_counts,
                "supporting_episode_ids": sorted(supporting_episode_ids),
                "reranked_hit_ids": [hit.hit_id for hit in reranked_hits],
            },
            structured_context=structured_context,
        )

    def _resolve_visibility(
        self,
        graph: TemporalContextGraph,
        query: GraphQuery,
    ) -> tuple[list[str], set[str]]:
        base_visible_episode_ids = graph.visible_episode_ids(query.max_chapter, query.max_paragraph)
        if query.window_mode == "recent":
            visible_episode_ids = base_visible_episode_ids[-query.recent_episode_count :]
        else:
            visible_episode_ids = base_visible_episode_ids

        visible_entity_ids = {
            entity_id
            for entity_id, entity in graph.entities.items()
            if (
                query.max_chapter is None
                or entity.first_seen_chapter < query.max_chapter
                or (
                    entity.first_seen_chapter == query.max_chapter
                    and (query.max_paragraph is None or entity.first_seen_paragraph <= query.max_paragraph)
                )
            )
            and (query.min_chapter is None or entity.last_seen_chapter >= query.min_chapter)
        }
        if query.window_mode == "recent":
            recent_entity_ids = set()
            for episode_id in visible_episode_ids:
                recent_entity_ids.update(graph.episodes[episode_id].entity_ids)
            visible_entity_ids = visible_entity_ids.intersection(recent_entity_ids)
        return visible_episode_ids, visible_entity_ids

    def _graph_expand_hits(
        self,
        *,
        graph: TemporalContextGraph,
        query: GraphQuery,
        visible_episode_ids: list[str],
        visible_entity_ids: set[str],
        seed_episode_hits: list[GraphHit],
        seed_entity_hits: list[GraphHit],
    ) -> list[GraphHit]:
        hits: list[GraphHit] = []
        seen_ids = {hit.hit_id for hit in seed_episode_hits + seed_entity_hits}
        visible_episode_set = set(visible_episode_ids)
        for hit in seed_episode_hits[:2]:
            episode = graph.episodes.get(hit.hit_id)
            if episode is None:
                continue
            for entity_id in episode.entity_ids:
                if entity_id not in visible_entity_ids or entity_id in seen_ids:
                    continue
                entity = graph.entities[entity_id]
                hits.append(
                    GraphHit(
                        hit_id=entity_id,
                        hit_type="entity",
                        score=round(hit.score * 0.7, 4),
                        reason="graph_bfs_from_episode",
                        chapter_index=entity.first_seen_chapter,
                        payload={
                            "canonical_name": entity.canonical_name,
                            "entity_type": entity.entity_type,
                            "summary": entity.summary,
                            "episode_ids": entity.episode_ids[:6],
                        },
                        provenance=[
                            graph.episodes[episode_id].provenance[0]
                            for episode_id in entity.episode_ids[:2]
                            if episode_id in graph.episodes and graph.episodes[episode_id].provenance
                        ],
                    )
                )
                seen_ids.add(entity_id)
        for hit in seed_entity_hits[:3]:
            entity_id = hit.hit_id
            for neighbor in graph.entity_neighbors(entity_id):
                neighbor_id = neighbor["entity_id"]
                if neighbor_id not in visible_entity_ids or neighbor_id in seen_ids:
                    continue
                relation_matches = graph.relation_lookup(entity_id, neighbor_id, include_invalidated=query.include_invalidated_relations)
                relation_matches = [
                    relation
                    for relation in relation_matches
                    if set(relation.episode_ids).intersection(visible_episode_set)
                ]
                if not relation_matches:
                    continue
                relation = relation_matches[-1]
                hits.append(
                    GraphHit(
                        hit_id=relation.edge_id,
                        hit_type="relation",
                        score=round(hit.score * 0.65, 4),
                        reason="graph_bfs_from_entity",
                        chapter_index=relation.valid_at_chapter,
                        payload={
                            "source_entity_id": relation.source_entity_id,
                            "target_entity_id": relation.target_entity_id,
                            "relation_type": relation.relation_type,
                            "state_family": relation.state_family,
                            "fact": relation.fact,
                            "weight": relation.weight,
                            "episode_ids": relation.episode_ids[:6],
                        },
                        provenance=relation.provenance[:2],
                    )
                )
                seen_ids.add(relation.edge_id)
        return hits

    def _rerank_hits(self, search_results: dict[str, list[GraphHit]], query: GraphQuery) -> list[GraphHit]:
        all_hits = [hit for hits in search_results.values() for hit in hits]
        merged: dict[str, GraphHit] = {}
        for hit in all_hits:
            existing = merged.get(hit.hit_id)
            if existing is None or hit.score > existing.score:
                merged[hit.hit_id] = hit

        node_type_quota = {
            "episode": max(2, query.top_k // 2),
            "relation": 3,
            "entity": 3,
            "community": 0,  # removed
            "saga": 0,  # removed
            "chapter": 2,
        }
        buckets: dict[str, list[GraphHit]] = {}
        for hit in merged.values():
            adjusted_score = hit.score
            if hit.hit_type == "relation":
                adjusted_score += 0.25
            elif hit.hit_type == "entity":
                adjusted_score += 0.15
            hit.score = round(adjusted_score, 4)
            buckets.setdefault(hit.hit_type, []).append(hit)

        reranked: list[GraphHit] = []
        for hit_type, hits in buckets.items():
            hits.sort(key=lambda item: (item.score, -(item.chapter_index or 0)), reverse=True)
            reranked.extend(hits[: node_type_quota.get(hit_type, 2)])

        reranked.sort(key=lambda item: (item.score, -(item.chapter_index or 0)), reverse=True)
        return reranked[: max(query.top_k, 6) + 6]

    def _construct_context(self, graph: TemporalContextGraph, hits: list[GraphHit]) -> dict[str, Any]:
        visible_facts: list[dict[str, Any]] = []
        entities: list[dict[str, Any]] = []
        citations: list[dict[str, Any]] = []

        for hit in hits:
            if hit.provenance:
                provenance = hit.provenance[0]
                citations.append(
                    {
                        "chunk_id": provenance.chunk_id,
                        "chapter_index": provenance.chapter_index,
                        "paragraph_index": provenance.paragraph_index,
                        "source": provenance.source,
                    }
                )
            if hit.hit_type == "relation":
                payload = dict(hit.payload)
                source = graph.entities.get(payload.get("source_entity_id"))
                target = graph.entities.get(payload.get("target_entity_id"))
                visible_facts.append(
                    {
                        "relation_id": hit.hit_id,
                        "fact": payload.get("fact") or hit.reason,
                        "relation_type": payload.get("relation_type"),
                        "state_family": payload.get("state_family"),
                        "story_timeline": {
                            "chapter": payload.get("valid_at_chapter"),
                            "paragraph": payload.get("valid_at_paragraph"),
                        },
                        "source_name": source.canonical_name if source else payload.get("source_entity_id"),
                        "target_name": target.canonical_name if target else payload.get("target_entity_id"),
                        "provenance": [item.model_dump() for item in hit.provenance],
                    }
                )
            elif hit.hit_type == "entity":
                entities.append(
                    {
                        "entity_id": hit.hit_id,
                        "canonical_name": hit.payload.get("canonical_name"),
                        "entity_type": hit.payload.get("entity_type"),
                        "summary": hit.payload.get("summary"),
                        "provenance": [item.model_dump() for item in hit.provenance],
                    }
                )

        return {
            "visible_facts": visible_facts[:8],
            "entities": entities[:6],
            "local_communities": [],
            "long_arcs": [],
            "citations": citations[:10],
        }

    def _retrieve_episodes(
        self,
        graph: TemporalContextGraph,
        query: GraphQuery,
        query_tokens: list[str],
        visible_episode_ids: list[str],
        requested_entity_terms: set[str],
        requested_tags: set[str],
    ) -> list[GraphHit]:
        hits: list[GraphHit] = []
        for episode_id in visible_episode_ids:
            episode = graph.episodes[episode_id]
            if query.min_chapter is not None and episode.chapter_index < query.min_chapter:
                continue
            if (
                query.min_paragraph is not None
                and query.min_chapter is not None
                and episode.chapter_index == query.min_chapter
                and episode.paragraph_index < query.min_paragraph
            ):
                continue
            if requested_tags and not requested_tags.intersection(episode.tags):
                continue
            if query.metadata_filters and not _matches_metadata(episode.metadata, query.metadata_filters):
                continue

            entity_bonus = 0.0
            if requested_entity_terms:
                entity_names = {
                    graph.entities[entity_id].canonical_name.lower()
                    for entity_id in episode.entities
                    if entity_id in graph.entities
                }
                matches = requested_entity_terms.intersection(entity_names)
                entity_bonus = 0.75 * len(matches)
                if not matches:
                    continue

            metadata_bonus = 0.25 * sum(
                1
                for key, value in query.metadata_filters.items()
                if episode.metadata.get(key) == value
            )
            tag_bonus = 0.2 * len(requested_tags.intersection(episode.tags))
            score = _text_score(query_tokens, episode.text) + entity_bonus + metadata_bonus + tag_bonus
            if score <= 0:
                continue

            hits.append(
                GraphHit(
                    hit_id=episode_id,
                    hit_type="episode",
                    score=round(score, 4),
                    reason="episode_text+entity+metadata",
                    chapter_index=episode.chapter_index,
                    payload={
                        "chunk_id": episode.chunk_id,
                        "entities": episode.entity_ids,
                        "tags": episode.tags,
                        "spoiler_level": episode.spoiler_level,
                        "paragraph_id": episode.paragraph_index,
                        "reference_time": episode.reference_time,
                    },
                    provenance=episode.provenance,
                )
            )
        return hits

    def _retrieve_chapters(
        self,
        graph: TemporalContextGraph,
        query: GraphQuery,
        supporting_episode_ids: set[str],
        query_tokens: list[str],
    ) -> list[GraphHit]:
        hits: list[GraphHit] = []
        for chapter in graph.chapters.values():
            if query.max_chapter is not None and chapter.chapter_index > query.max_chapter:
                continue
            if query.min_chapter is not None and chapter.chapter_index < query.min_chapter:
                continue
            if not supporting_episode_ids.intersection(chapter.episode_ids):
                continue
            score = _text_score(query_tokens, f"{chapter.title} {chapter.metadata.get('timeline_summary', '')}")
            score += min(len(supporting_episode_ids.intersection(chapter.episode_ids)), 3) * 0.35
            if score <= 0:
                continue
            hits.append(
                GraphHit(
                    hit_id=chapter.chapter_node_id,
                    hit_type="chapter",
                    score=round(score, 4),
                    reason="chapter_timeline_overlap",
                    chapter_index=chapter.chapter_index,
                    payload={
                        "chapter_id": chapter.chapter_id,
                        "title": chapter.title,
                        "entity_ids": chapter.entity_ids,
                        "relation_ids": chapter.relation_ids,
                        "active_relation_ids": chapter.active_relation_ids,
                        "invalidated_relation_ids": chapter.invalidated_relation_ids,
                        "paragraph_count": chapter.paragraph_count,
                    },
                    provenance=chapter.provenance[:3],
                )
            )
        return sorted(hits, key=lambda item: item.score, reverse=True)[:3]

    def _retrieve_entities(
        self,
        graph: TemporalContextGraph,
        query: GraphQuery,
        visible_entity_ids: set[str],
        supporting_episode_ids: set[str],
        query_tokens: list[str],
    ) -> list[GraphHit]:
        hits: list[GraphHit] = []
        requested_entities = {value.lower() for value in query.entity_names}
        allowed_types = set(query.entity_types)
        for entity_id in visible_entity_ids:
            entity = graph.entities[entity_id]
            if entity.mention_count < query.min_entity_mentions:
                continue
            if allowed_types and entity.entity_type not in allowed_types:
                continue
            searchable_text = " ".join([entity.canonical_name, *entity.aliases, entity.entity_type])
            score = _text_score(query_tokens, searchable_text)
            if requested_entities and entity.canonical_name.lower() in requested_entities:
                score += 1.0
            support_overlap = supporting_episode_ids.intersection(entity.episode_ids)
            if support_overlap:
                score += min(len(support_overlap), 3) * 0.3
            if score <= 0:
                continue
            hits.append(
                GraphHit(
                    hit_id=entity.entity_id,
                    hit_type="entity",
                    score=round(score, 4),
                    reason="entity_name+episode_overlap",
                    chapter_index=entity.first_seen_chapter,
                    payload={
                    "canonical_name": entity.canonical_name,
                    "entity_type": entity.entity_type,
                    "mention_count": entity.mention_count,
                    "summary": entity.summary,
                    "episode_ids": entity.episode_ids[:6],
                    "neighbor_count": len(graph.entity_neighbors(entity.entity_id)),
                },
                    provenance=[
                        graph.episodes[episode_id].provenance[0]
                        for episode_id in entity.episode_ids[:3]
                        if episode_id in graph.episodes and graph.episodes[episode_id].provenance
                    ],
                )
            )
        return sorted(hits, key=lambda item: item.score, reverse=True)[:3]

    def _retrieve_relations(
        self,
        graph: TemporalContextGraph,
        query: GraphQuery,
        visible_episode_ids: list[str],
        visible_entity_ids: set[str],
        supporting_episode_ids: set[str],
        query_tokens: list[str],
    ) -> list[GraphHit]:
        hits: list[GraphHit] = []
        visible_episodes = set(visible_episode_ids)
        requested_entities = {value.lower() for value in query.entity_names}
        allowed_relation_types = set(query.relation_types)
        allowed_state_families = set(query.state_families)
        allowed_statuses = set(query.relation_statuses)
        for edge in graph.relations.values():
            if edge.weight < query.min_relation_weight:
                continue
            if allowed_statuses and edge.status not in allowed_statuses:
                continue
            if not query.include_invalidated_relations and edge.status == "invalidated":
                continue
            if allowed_relation_types and edge.relation_type not in allowed_relation_types:
                continue
            if allowed_state_families and edge.state_family not in allowed_state_families:
                continue
            if edge.source_entity_id not in visible_entity_ids or edge.target_entity_id not in visible_entity_ids:
                continue
            if not edge.overlaps_window(
                min_chapter=query.min_chapter,
                max_chapter=query.max_chapter,
                max_paragraph=query.max_paragraph,
            ):
                continue
            if not visible_episodes.intersection(edge.episode_ids):
                continue

            source_name = graph.entities[edge.source_entity_id].canonical_name
            target_name = graph.entities[edge.target_entity_id].canonical_name
            name_text = f"{source_name} {target_name} {edge.relation_type} {edge.fact}"
            score = _text_score(query_tokens, name_text) + min(edge.weight, 3.0) * 0.2
            if requested_entities and {source_name.lower(), target_name.lower()}.intersection(requested_entities):
                score += 0.8
            if supporting_episode_ids.intersection(edge.episode_ids):
                score += 0.6
            if edge.status == "active":
                score += 0.2
            if score <= 0:
                continue
            hits.append(
                GraphHit(
                    hit_id=edge.edge_id,
                    hit_type="relation",
                    score=round(score, 4),
                    reason="edge_validity+entity_overlap",
                    chapter_index=edge.validity_start_chapter,
                    payload={
                        "source_entity_id": edge.source_entity_id,
                        "target_entity_id": edge.target_entity_id,
                        "relation_type": edge.relation_type,
                        "state_family": edge.state_family,
                        "fact": edge.fact,
                        "status": edge.status,
                        "valid_at_chapter": edge.valid_at_chapter,
                        "valid_at_paragraph": edge.valid_at_paragraph,
                        "invalid_at_chapter": edge.invalid_at_chapter,
                        "invalid_at_paragraph": edge.invalid_at_paragraph,
                        "invalidated_by_edge_id": edge.invalidated_by_edge_id,
                        "episode_ids": edge.episode_ids,
                        "weight": edge.weight,
                        "reference_time": edge.reference_time,
                    },
                    provenance=edge.provenance[:3],
                )
            )
        return sorted(hits, key=lambda item: item.score, reverse=True)[:3]

    def _retrieve_communities(
        self,
        graph: TemporalContextGraph,
        query: GraphQuery,
        supporting_episode_ids: set[str],
        query_tokens: list[str],
    ) -> list[GraphHit]:
        hits: list[GraphHit] = []
        for community in graph.communities.values():
            if query.max_chapter is not None and community.chapter_start > query.max_chapter:
                continue
            if query.min_chapter is not None and community.chapter_end < query.min_chapter:
                continue
            if not supporting_episode_ids.intersection(community.episode_ids):
                continue
            label_score = _text_score(query_tokens, community.label)
            score = label_score + _text_score(query_tokens, community.summary) + 0.3 * min(len(community.entity_ids), 4)
            hits.append(
                GraphHit(
                    hit_id=community.community_id,
                    hit_type="community",
                    score=round(score, 4),
                    reason="community_overlap",
                    chapter_index=community.chapter_start,
                    payload={
                        "label": community.label,
                        "summary": community.summary,
                        "entity_ids": community.entity_ids,
                        "episode_ids": community.episode_ids[:6],
                        "relation_ids": community.relation_ids[:6],
                    },
                    provenance=community.provenance[:3],
                )
            )
        return sorted(hits, key=lambda item: item.score, reverse=True)[:2]

    def _retrieve_sagas(
        self,
        graph: TemporalContextGraph,
        query: GraphQuery,
        supporting_episode_ids: set[str],
        query_tokens: list[str],
    ) -> list[GraphHit]:
        hits: list[GraphHit] = []
        for saga in graph.sagas.values():
            if query.max_chapter is not None and saga.chapter_start > query.max_chapter:
                continue
            if query.min_chapter is not None and saga.chapter_end < query.min_chapter:
                continue
            if not supporting_episode_ids.intersection(saga.episode_ids):
                continue
            score = _text_score(query_tokens, f"{saga.label} {saga.summary}") + 0.25 * len(
                supporting_episode_ids.intersection(saga.episode_ids)
            )
            hits.append(
                GraphHit(
                    hit_id=saga.saga_id,
                    hit_type="saga",
                    score=round(score, 4),
                    reason="saga_temporal_context",
                    chapter_index=saga.chapter_start,
                    payload={
                        "label": saga.label,
                        "summary": saga.summary,
                        "chapter_start": saga.chapter_start,
                        "chapter_end": saga.chapter_end,
                        "entity_ids": saga.entity_ids,
                        "relation_ids": saga.relation_ids[:6],
                    },
                    provenance=saga.provenance[:3],
                )
            )
        return sorted(hits, key=lambda item: item.score, reverse=True)[:2]


def search_temporal_graph(
    graph: TemporalContextGraph,
    query: str,
    max_chapter: int,
    max_paragraph: int | None = None,
    top_k: int = 5,
) -> list[GraphHit]:
    result = TemporalGraphRetriever().retrieve(
        graph,
        GraphQuery(query=query, max_chapter=max_chapter, max_paragraph=max_paragraph, top_k=top_k),
    )
    return result.hits
