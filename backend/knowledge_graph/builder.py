from __future__ import annotations

import json
import re
import time
from collections import Counter, defaultdict
from datetime import UTC, datetime
from typing import Callable

from backend.api.schemas import BookChunk, BookRecord
from backend.config import GRAPHS_DIR

from . import llm_extraction
from . import build_logger as build_logger_mod
from . import storage as graph_storage
from .extraction_window import ExtractionWindow, build_extraction_windows
from .models import (
    ChapterNode,
    ChapterTimelineEntry,
    CommunityNode,
    EntityNode,
    EpisodeNode,
    FactCandidate,
    GraphProvenance,
    RelationDirectionality,
    RelationEdge,
    RelationStatus,
    SagaNode,
    TemporalContextGraph,
)
from .relation_schema import build_state_slot, normalize_fact_candidate


STATEFUL_FAMILIES = {"location", "membership", "status"}
CHAPTER_CONSOLIDATION_FAMILIES = {"location", "membership", "status", "interaction", "identity"}


def _slugify(value: str) -> str:
    lowered = value.strip().lower()
    lowered = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "_", lowered)
    return lowered.strip("_") or "unknown"


def _excerpt(text: str, limit: int = 180) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return f"{compact[: limit - 3]}..."


def _chapter_title(chunk: BookChunk) -> str:
    metadata = chunk.metadata or {}
    title = metadata.get("chapter_title")
    if isinstance(title, str) and title.strip():
        return title.strip()
    return chunk.chapter_id.replace("_", " ").title()


def _build_provenance(chunk: BookChunk, source: str, extra_metadata: dict | None = None) -> GraphProvenance:
    metadata = dict(extra_metadata or {})
    metadata.setdefault("spoiler_guard", chunk.spoiler_guard)
    metadata.setdefault("chunk_level", chunk.chunk_level)
    metadata.setdefault("section_id", chunk.section_id)
    return GraphProvenance(
        chunk_id=chunk.chunk_id,
        book_id=chunk.book_id,
        chapter_id=chunk.chapter_id,
        chapter_index=chunk.chapter_index,
        paragraph_id=chunk.paragraph_id,
        paragraph_index=chunk.paragraph_index,
        text_excerpt=_excerpt(chunk.text),
        episode_id=metadata.get("episode_id"),
        evidence_text=str(metadata.get("sentence", "") or metadata.get("evidence_text", "")).strip(),
        evidence_start=metadata.get("evidence_start"),
        evidence_end=metadata.get("evidence_end"),
        source=source,  # type: ignore[arg-type]
        metadata=metadata,
    )


def _normalize_entity_name(name: str) -> str:
    return " ".join(name.strip().split())


def _infer_entity_type(name: str, declared_type: str, chunk: BookChunk) -> str:
    allowed = {"character", "location", "concept", "group", "theme", "artifact", "unknown"}
    if declared_type in allowed:
        return declared_type
    return "unknown"


def _entity_aliases(name: str) -> list[str]:
    aliases = {name}
    parts = name.split()
    if len(parts) > 1:
        aliases.add(parts[-1])
    return sorted(alias for alias in aliases if alias)


_HONORIFIC_PREFIXES = ("堂", "唐")  # Only unambiguous honorifics. "小"/"老" are ambiguous (can indicate different person in Chinese).
_TITLE_SUFFIXES = ("上校", "将军", "第二", "第一", "先生", "夫人", "小姐", "女士", "上尉", "中尉")


def _normalize_name_for_matching(name: str) -> set[str]:
    """Generate slug variants of a name for fuzzy matching.

    Strips honorific prefixes (堂, 老, 小) and title suffixes (上校, 将军)
    to catch cases like 堂何塞·阿尔卡蒂奥·布恩迪亚 -> 何塞·阿尔卡蒂奥·布恩迪亚.
    """
    variants = {name}
    stripped = name
    for prefix in _HONORIFIC_PREFIXES:
        if stripped.startswith(prefix) and len(stripped) > len(prefix) + 1:
            stripped = stripped[len(prefix):]
            variants.add(stripped)
            break
    for suffix in _TITLE_SUFFIXES:
        if stripped.endswith(suffix) and len(stripped) > len(suffix) + 1:
            stripped = stripped[:-len(suffix)].rstrip("· ")
            variants.add(stripped)
    # Also try without middle-dot separators
    no_dot = name.replace("·", " ").replace("·", " ")
    if no_dot != name:
        variants.add(no_dot)
    return {_slugify(v) for v in variants if v.strip()}




def _fact_signature(
    relation_type: str,
    state_family: str,
    source_entity_id: str,
    target_entity_id: str,
    directionality: RelationDirectionality,
) -> str:
    if directionality == "undirected":
        pair = sorted((source_entity_id, target_entity_id))
        return f"{relation_type}|{state_family}|{pair[0]}|{pair[1]}"
    return f"{relation_type}|{state_family}|{source_entity_id}|{target_entity_id}"


def _state_key(state_family: str, source_entity_id: str, directionality: RelationDirectionality, target_entity_id: str) -> str:
    if state_family not in STATEFUL_FAMILIES:
        return ""
    if directionality == "undirected":
        ordered = sorted((source_entity_id, target_entity_id))
        return f"{state_family}|{ordered[0]}|{ordered[1]}"
    return f"{state_family}|{source_entity_id}"


def _entity_alias_forms(entity: EntityNode) -> set[str]:
    return {
        _slugify(entity.canonical_name),
        *(_slugify(alias) for alias in entity.aliases),
    }


class TemporalGraphBuilder:
    """Build a Graphiti-style temporal knowledge graph from paragraph episodes."""

    def __init__(
        self,
        extractor_runtime: llm_extraction.GraphExtractorRuntime | None = None,
        progress_callback: Callable[[dict], None] | None = None,
        strict_llm_extraction: bool = False,
        build_logger: build_logger_mod.GraphBuildLogger | None = None,
        use_description_cache: bool = True,
        use_generation_info: bool = True,
    ) -> None:
        self.extractor_runtime = extractor_runtime or llm_extraction.resolve_graph_extractor_runtime()
        self.progress_callback = progress_callback
        self.strict_llm_extraction = strict_llm_extraction
        self.build_logger = build_logger
        self.use_description_cache = use_description_cache
        self.use_generation_info = use_generation_info

    def build(self, book: BookRecord) -> TemporalContextGraph:
        if self.strict_llm_extraction and self.extractor_runtime is None:
            raise RuntimeError(
                "strict Graphiti extraction requires GRAPHITI_EXTRACTOR_API_KEY, "
                "GRAPHITI_EXTRACTOR_BASE_URL and GRAPHITI_EXTRACTOR_MODEL_NAME."
            )
        now = datetime.now(UTC).isoformat()
        extraction_backend = "llm-assisted-resolution" if self.extractor_runtime is not None else "none"

        sorted_chunks = sorted(book.chunks, key=lambda item: (item.chapter_index, item.paragraph_index))
        total_chunks = len(sorted_chunks)
        processed_chunk_ids: set[str] = set()
        new_entity_ids: set[str] = set()
        previous_episode_id: str | None = None

        # --- resume from checkpoint if a partial graph exists ---
        if graph_storage.graph_exists(book.book_id):
            graph = graph_storage.load_graph(book.book_id)
            processed_chunk_ids = {ep.chunk_id for ep in graph.episodes.values() if ep.chunk_id}
            entity_id_by_alias, active_relation_by_signature, active_state_relation_by_key, relation_version_counter = (
                self._rebuild_state_from_graph(graph)
            )
            chapter_entities, chapter_episode_ids, chapter_relation_ids = self._rebuild_chapter_indexes(graph)
            chapter_active_relation_ids: dict[int, set[str]] = defaultdict(set)
            chapter_invalidated_relation_ids: dict[int, set[str]] = defaultdict(set)
            chapter_provenance: dict[int, list[GraphProvenance]] = defaultdict(list)
            chapter_paragraph_count: Counter[int] = Counter()
            for ep in graph.episodes.values():
                chapter_episode_ids[ep.chapter_index].append(ep.episode_id)
                chapter_paragraph_count[ep.chapter_index] += 1
            # find previous episode
            sorted_eps = sorted(graph.episodes.values(), key=lambda e: e.episode_index)
            if sorted_eps:
                previous_episode_id = sorted_eps[-1].episode_id
            remaining = total_chunks - len(processed_chunk_ids)
            if self.build_logger is not None:
                self.build_logger.build_start(
                    total_chunks=total_chunks,
                    extraction_backend=extraction_backend,
                )
        else:
            graph = TemporalContextGraph(
                graph_id=f"graph::{book.book_id}",
                book_id=book.book_id,
                title=book.title,
                metadata={
                    "source_path": book.source_path,
                    "chapter_count": book.chapter_count,
                    "chunk_count": total_chunks,
                    "builder": "TemporalGraphBuilder",
                    "graph_style": "graphiti-inspired",
                    "entity_extraction": extraction_backend,
                    "fact_extraction": extraction_backend,
                    "created_at": now,
                    "llm_calls": 0,
                    "llm_skipped": 0,
                    "chapter_consolidations": [],
                },
            )
            entity_id_by_alias: dict[str, str] = {}
            active_relation_by_signature: dict[str, str] = {}
            active_state_relation_by_key: dict[str, str] = {}
            relation_version_counter: Counter[str] = Counter()
            chapter_entities: dict[int, set[str]] = defaultdict(set)
            chapter_episode_ids: dict[int, list[str]] = defaultdict(list)
            chapter_relation_ids: dict[int, set[str]] = defaultdict(set)
            chapter_active_relation_ids: dict[int, set[str]] = defaultdict(set)
            chapter_invalidated_relation_ids: dict[int, set[str]] = defaultdict(set)
            chapter_provenance: dict[int, list[GraphProvenance]] = defaultdict(list)
            chapter_paragraph_count: Counter[int] = Counter()
            if self.build_logger is not None:
                self.build_logger.build_start(
                    total_chunks=total_chunks,
                    extraction_backend=extraction_backend,
                )

        if self.build_logger is not None:
            self.build_logger.build_start(
                total_chunks=total_chunks,
                extraction_backend=extraction_backend,
            )

        # --- build sliding-window extractions (one LLM call per window) ---
        chunk_extractions: dict[str, llm_extraction.EpisodeGraphExtraction | None] = {}
        if self.extractor_runtime is not None:
            windows = build_extraction_windows(sorted_chunks)
            total_windows = len(windows)

            # --- window checkpoint: load previously completed windows ---
            win_checkpoint_path = GRAPHS_DIR / f"{book.book_id}.windows.jsonl"
            completed_window_ids: set[str] = set()
            if win_checkpoint_path.exists():
                for line in win_checkpoint_path.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                        wid = record["window_id"]
                        completed_window_ids.add(wid)
                        for item in record.get("chunks", []):
                            cid = item["chunk_id"]
                            if cid not in processed_chunk_ids:
                                chunk_extractions[cid] = llm_extraction.EpisodeGraphExtraction.model_validate(item["extraction"])
                    except (json.JSONDecodeError, KeyError):
                        continue

            for wi, window in enumerate(windows):
                if window.window_id in completed_window_ids:
                    continue  # already processed in a previous run
                self._emit_progress(
                    stage="window-extraction",
                    title=f"Extracting window {wi+1}/{total_windows}",
                    message=(
                        f"Window {window.window_id}: {len(window.core_chunks)} chunks, "
                        f"{window.core_token_count} core tokens + {window.prev_context_token_count} context tokens."
                    ),
                    processed_snippets=wi,
                    total_snippets=total_windows,
                    current_snippet_id=window.window_id,
                    current_chapter_index=window.chapter_index,
                    details={
                        "phase": "window-extraction",
                        "window_id": window.window_id,
                        "core_token_count": window.core_token_count,
                        "prev_context_token_count": window.prev_context_token_count,
                        "chunk_count": len(window.core_chunks),
                    },
                )
                extraction = self._extract_window_with_llm(window=window, graph=graph)
                for chunk in window.core_chunks:
                    if chunk.chunk_id not in processed_chunk_ids:
                        chunk_extractions[chunk.chunk_id] = extraction
                # persist window extraction to checkpoint
                checkpoint_record = {
                    "window_id": window.window_id,
                    "chunks": [
                        {
                            "chunk_id": chunk.chunk_id,
                            "extraction": extraction.model_dump(mode="json") if extraction else None,
                        }
                        for chunk in window.core_chunks
                    ],
                }
                with open(win_checkpoint_path, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(checkpoint_record, ensure_ascii=False) + "\n")
                graph_storage.save_graph(graph)
                time.sleep(3.0)  # avoid API rate-limiting
        else:
            windows = []

        for episode_index, chunk in enumerate(sorted_chunks, start=1):
            if chunk.chunk_id in processed_chunk_ids:
                continue
            llm_episode_extraction = chunk_extractions.get(chunk.chunk_id)
            self._emit_progress(
                stage="graph-episode-start",
                title="Processing graph episode",
                message=(
                    f"已处理文段 {episode_index - 1}/{total_chunks}，"
                    f"当前进入 chapter {chunk.chapter_index} paragraph {chunk.paragraph_index} 的图谱构建。"
                ),
                processed_snippets=episode_index - 1,
                total_snippets=total_chunks,
                current_snippet_id=chunk.chunk_id,
                current_chapter_index=chunk.chapter_index,
                current_paragraph_index=chunk.paragraph_index,
                details={
                    "phase": "episode-start",
                    "source_paragraph_indices": chunk.metadata.get("source_paragraph_indices", []),
                    "source_paragraph_count": chunk.metadata.get("source_paragraph_count", 1),
                    "packet_token_count": chunk.metadata.get("packet_token_count", len(chunk.text)),
                    "is_merged_packet": chunk.metadata.get("is_merged_packet", False),
                },
            )
            if self.build_logger is not None:
                self.build_logger.episode_start(
                    chunk_id=chunk.chunk_id,
                    chapter=chunk.chapter_index,
                    paragraph=chunk.paragraph_index,
                    text=chunk.text,
                    token_count=int(chunk.metadata.get("packet_token_count", len(chunk.text)) or 0),
                    source_para_count=int(chunk.metadata.get("source_paragraph_count", 1) or 1),
                    is_merged=bool(chunk.metadata.get("is_merged_packet", False)),
                )
                self.build_logger.flush()
            if self.build_logger is not None:
                gate = chunk.metadata.get("llm_gate", {})
                llm_called = llm_episode_extraction is not None
                self.build_logger.llm_decision(
                    chunk_id=chunk.chunk_id,
                    called=llm_called,
                    score=int(gate.get("score", 0)),
                    reasons=gate.get("reasons", []),
                )
                if llm_called and llm_episode_extraction is not None:
                    self.build_logger.llm_response(
                        chunk_id=chunk.chunk_id,
                        entity_count=len(llm_episode_extraction.entities),
                        fact_count=len(llm_episode_extraction.facts),
                        entities=[
                            {"name": e.canonical_name, "type": e.entity_type, "aliases": e.aliases}
                            for e in llm_episode_extraction.entities
                        ],
                        facts=[
                            {"source": f.source, "target": f.target,
                             "relation": f.relation_type, "fact": f.fact}
                            for f in llm_episode_extraction.facts
                        ],
                        raw_response=getattr(llm_episode_extraction, "raw_response", ""),
                    )
                self.build_logger.flush()
            chapter_node_id = f"chapter_{chunk.chapter_index:03d}"
            chapter = graph.chapters.get(chapter_node_id)
            if chapter is None:
                chapter = ChapterNode(
                    chapter_node_id=chapter_node_id,
                    book_id=book.book_id,
                    chapter_id=chunk.chapter_id,
                    chapter_index=chunk.chapter_index,
                    title=_chapter_title(chunk),
                    spoiler_level=chunk.spoiler_level,
                    metadata={"section_ids": [], "chunk_levels": [], "reference_time": f"chapter://{book.book_id}/{chunk.chapter_index:03d}"},
                    provenance=[],
                )
                graph.chapters[chapter_node_id] = chapter

            episode_id = f"episode_{chunk.chapter_index:03d}_{chunk.paragraph_index:03d}"
            reference_time = f"narrative://{book.book_id}/c{chunk.chapter_index:03d}/p{chunk.paragraph_index:03d}"
            episode = EpisodeNode(
                episode_id=episode_id,
                episode_type="paragraph",
                book_id=book.book_id,
                chunk_id=chunk.chunk_id,
                chapter_id=chunk.chapter_id,
                chapter_index=chunk.chapter_index,
                paragraph_id=chunk.paragraph_id,
                paragraph_index=chunk.paragraph_index,
                episode_index=episode_index,
                text=chunk.text,
                spoiler_level=chunk.spoiler_level,
                tags=list(chunk.tags),
                reference_time=reference_time,
                created_at=now,
                metadata={
                    "token_offset": chunk.token_offset,
                    "position": chunk.position,
                    "section_id": chunk.section_id,
                    "paragraph_start_id": chunk.paragraph_start_id,
                    "paragraph_end_id": chunk.paragraph_end_id,
                    "extraction_mode": (
                        llm_episode_extraction.extraction_mode if llm_episode_extraction is not None else "skipped"
                    ),
                    **chunk.metadata,
                },
                provenance=[_build_provenance(chunk, "episode", {"reference_time": reference_time})],
            )
            if previous_episode_id is not None:
                episode.prev_episode_id = previous_episode_id
                graph.episodes[previous_episode_id].next_episode_id = episode_id
            previous_episode_id = episode_id

            graph.episodes[episode_id] = episode
            chapter.episode_ids.append(episode_id)
            chapter.paragraph_count += 1
            chapter.spoiler_level = max(chapter.spoiler_level, chunk.spoiler_level)
            if chunk.section_id and chunk.section_id not in chapter.metadata["section_ids"]:
                chapter.metadata["section_ids"].append(chunk.section_id)
            if chunk.chunk_level not in chapter.metadata["chunk_levels"]:
                chapter.metadata["chunk_levels"].append(chunk.chunk_level)
            chapter.provenance.extend(episode.provenance)
            chapter_episode_ids[chunk.chapter_index].append(episode_id)
            chapter_provenance[chunk.chapter_index].extend(episode.provenance)
            chapter_paragraph_count[chunk.chapter_index] += 1

            existing_before = set(graph.entities.keys())
            entity_nodes = self._resolve_entities(
                chunk,
                graph,
                entity_id_by_alias,
                llm_episode_extraction=llm_episode_extraction,
            )
            entity_ids = [entity.entity_id for entity in entity_nodes]
            new_entity_ids.update(set(entity_ids) - existing_before)
            episode.entity_ids = entity_ids
            chapter.entity_ids = sorted(set(chapter.entity_ids).union(entity_ids))
            chapter_entities[chunk.chapter_index].update(entity_ids)

            for entity in entity_nodes:
                if episode_id not in entity.episode_ids:
                    entity.episode_ids.append(episode_id)
                entity.mention_count += 1
                if entity.first_seen_chapter == 0 or chunk.chapter_index < entity.first_seen_chapter:
                    entity.first_seen_chapter = chunk.chapter_index
                    entity.first_seen_paragraph = chunk.paragraph_index
                if (
                    chunk.chapter_index > entity.last_seen_chapter
                    or (
                        chunk.chapter_index == entity.last_seen_chapter
                        and chunk.paragraph_index >= entity.last_seen_paragraph
                    )
                ):
                    entity.last_seen_chapter = chunk.chapter_index
                    entity.last_seen_paragraph = chunk.paragraph_index
                entity.metadata.setdefault("chapter_span", [])
                if chunk.chapter_index not in entity.metadata["chapter_span"]:
                    entity.metadata["chapter_span"].append(chunk.chapter_index)

            relation_ids = self._extract_and_resolve_relations(
                chunk=chunk,
                episode=episode,
                entity_nodes=entity_nodes,
                graph=graph,
                now=now,
                active_relation_by_signature=active_relation_by_signature,
                active_state_relation_by_key=active_state_relation_by_key,
                relation_version_counter=relation_version_counter,
                llm_episode_extraction=llm_episode_extraction,
            )
            episode.relation_ids = relation_ids
            chapter.relation_ids = sorted(set(chapter.relation_ids).union(relation_ids))
            chapter_relation_ids[chunk.chapter_index].update(relation_ids)
            self._emit_progress(
                stage="graph-episode-complete",
                title="Episode graph step completed",
                message=(
                    f"已完成文段 {episode_index}/{total_chunks}，"
                    f"当前文段的 entities / facts 已写入临时图状态。"
                ),
                processed_snippets=episode_index,
                total_snippets=total_chunks,
                current_snippet_id=chunk.chunk_id,
                current_chapter_index=chunk.chapter_index,
                current_paragraph_index=chunk.paragraph_index,
                details={
                    "phase": "episode-complete",
                    "entity_count": len(entity_ids),
                    "relation_count": len(relation_ids),
                    "source_paragraph_indices": chunk.metadata.get("source_paragraph_indices", []),
                    "source_paragraph_count": chunk.metadata.get("source_paragraph_count", 1),
                    "packet_token_count": chunk.metadata.get("packet_token_count", len(chunk.text)),
                    "is_merged_packet": chunk.metadata.get("is_merged_packet", False),
                },
            )
            if self.build_logger is not None:
                extraction_mode = (
                    llm_episode_extraction.extraction_mode
                    if llm_episode_extraction is not None
                    else "skipped"
                )
                self.build_logger.episode_end(
                    chunk_id=chunk.chunk_id,
                    extraction_mode=extraction_mode,
                    entity_count=len(entity_ids),
                    relation_count=len(relation_ids),
                    entity_names=[
                        graph.entities[eid].canonical_name
                        for eid in entity_ids if eid in graph.entities
                    ],
                    relations=[
                        {"source": graph.entities[graph.relations[rid].source_entity_id].canonical_name
                         if rid in graph.relations and graph.relations[rid].source_entity_id in graph.entities else "?",
                         "target": graph.entities[graph.relations[rid].target_entity_id].canonical_name
                         if rid in graph.relations and graph.relations[rid].target_entity_id in graph.entities else "?",
                         "relation": graph.relations[rid].relation_type if rid in graph.relations else "?",
                         "status": graph.relations[rid].status if rid in graph.relations else "?"}
                        for rid in relation_ids
                    ],
                )
                self.build_logger.flush()
            graph_storage.save_graph(graph)

        self._emit_progress(
            stage="chapter-consolidation",
            title="Consolidating chapter facts",
            message="Normalizing chapter-level aliases and relation state families before higher-level graph assembly.",
            processed_snippets=total_chunks,
            total_snippets=total_chunks,
            details={
                "phase": "chapter-consolidation",
                "chapter_count": len(chapter_entities),
                "active_entity_count": len(graph.entities),
                "active_relation_count": len(graph.relations),
            },
        )
        self._consolidate_chapters(
            graph=graph,
            chapter_entities=chapter_entities,
            entity_id_by_alias=entity_id_by_alias,
            active_relation_by_signature=active_relation_by_signature,
            active_state_relation_by_key=active_state_relation_by_key,
        )

        self._emit_progress(
            stage="graph-dependency-build",
            title="Building narrative dependency edges",
            message=f"正在为 {total_chunks} 个 episode 构建叙事依赖边。",
            processed_snippets=total_chunks,
            total_snippets=total_chunks,
            details={"phase": "dependency-build"},
        )
        self._build_dependency_edges(graph, chapter_episode_ids)

        for relation in graph.relations.values():
            chapter_relation_ids[relation.valid_at_chapter].add(relation.edge_id)
            if relation.status == "active":
                chapter_active_relation_ids[relation.valid_at_chapter].add(relation.edge_id)
            else:
                chapter_invalidated_relation_ids[relation.valid_at_chapter].add(relation.edge_id)

        self._attach_chapter_collections(graph)

        for entity in graph.entities.values():
            chapter_span = entity.metadata.get("chapter_span", [])
            entity.summary = self._entity_summary(entity, chapter_span, graph)
            entity.metadata["episode_count"] = len(set(entity.episode_ids))
            entity.metadata["alias_count"] = len(entity.aliases)

        graph.metadata["graph_stats"] = graph.stats().model_dump()
        graph.metadata["dependency_edge_count"] = sum(len(ep.depends_on) for ep in graph.episodes.values())
        graph.metadata["active_relation_count"] = sum(1 for edge in graph.relations.values() if edge.status == "active")
        graph.metadata["invalidated_relation_count"] = sum(1 for edge in graph.relations.values() if edge.status == "invalidated")

        # --- final entity dedup: merge entities whose canonical_name is another's alias ---
        post_merges = self._run_canonical_as_alias_scan(
            graph=graph, entity_id_by_alias=entity_id_by_alias,
        )
        if post_merges:
            graph.metadata["post_build_merges"] = post_merges
            # Refresh stats after merge
            graph.metadata["graph_stats"] = graph.stats().model_dump()

        self._emit_progress(
            stage="graph-build-finished",
            title="Temporal graph assembled",
            message="图谱内存结构已经构建完成，等待持久化写盘。",
            processed_snippets=total_chunks,
            total_snippets=total_chunks,
            details={
                "phase": "graph-build-finished",
                "entity_count": len(graph.entities),
                "relation_count": len(graph.relations),
                "dependency_edge_count": sum(len(ep.depends_on) for ep in graph.episodes.values()),
            },
        )
        if self.build_logger is not None:
            self.build_logger.build_end(
                {
                    "total_episodes": total_chunks,
                    "entity_count": len(graph.entities),
                    "relation_count": len(graph.relations),
                    "dependency_edge_count": sum(len(ep.depends_on) for ep in graph.episodes.values()),
                    "active_relation_count": int(graph.metadata.get("active_relation_count", 0)),
                    "invalidated_relation_count": int(graph.metadata.get("invalidated_relation_count", 0)),
                    "llm_calls": int(graph.metadata.get("llm_calls", 0)),
                    "llm_skipped": int(graph.metadata.get("llm_skipped", 0)),
                }
            )
            self.build_logger.close()
        graph_storage.save_graph(graph)
        return graph

    @staticmethod
    def _merge_entity_into(
        *,
        survivor: EntityNode,
        absorbed: EntityNode,
        graph: TemporalContextGraph,
        entity_id_by_alias: dict[str, str],
    ) -> None:
        """Merge absorbed entity into survivor. Absorbed entity is removed from graph."""
        # Transfer aliases
        for alias in absorbed.aliases:
            if alias not in survivor.aliases:
                survivor.aliases.append(alias)
        # Transfer episode tracking
        for ep_id in absorbed.episode_ids:
            if ep_id not in survivor.episode_ids:
                survivor.episode_ids.append(ep_id)
        # Update mention count
        survivor.mention_count += absorbed.mention_count
        # Update chapter span
        if absorbed.first_seen_chapter < survivor.first_seen_chapter:
            survivor.first_seen_chapter = absorbed.first_seen_chapter
            survivor.first_seen_paragraph = absorbed.first_seen_paragraph
        if absorbed.last_seen_chapter > survivor.last_seen_chapter:
            survivor.last_seen_chapter = absorbed.last_seen_chapter
            survivor.last_seen_paragraph = absorbed.last_seen_paragraph
        # Merge chapter spans
        for ch in absorbed.metadata.get("chapter_span", []):
            if ch not in survivor.metadata.setdefault("chapter_span", []):
                survivor.metadata["chapter_span"].append(ch)
        # Mark merge in metadata
        survivor.metadata.setdefault("merged_from", [])
        survivor.metadata["merged_from"].append(absorbed.entity_id)
        survivor.metadata["merge_count"] = survivor.metadata.get("merge_count", 0) + 1
        # Redirect all alias keys to survivor
        for alias in absorbed.aliases:
            key = _slugify(alias)
            if entity_id_by_alias.get(key) == absorbed.entity_id:
                entity_id_by_alias[key] = survivor.entity_id
        key = _slugify(absorbed.canonical_name)
        if entity_id_by_alias.get(key) == absorbed.entity_id:
            entity_id_by_alias[key] = survivor.entity_id
        # Re-point relations from absorbed to survivor
        for rel in graph.relations.values():
            if rel.source_entity_id == absorbed.entity_id:
                rel.source_entity_id = survivor.entity_id
            if rel.target_entity_id == absorbed.entity_id:
                rel.target_entity_id = survivor.entity_id
        # Remove absorbed from graph
        del graph.entities[absorbed.entity_id]

    def _run_canonical_as_alias_scan(
        self,
        graph: TemporalContextGraph,
        entity_id_by_alias: dict[str, str],
    ) -> int:
        """Scan for entities whose canonical_name is another entity's alias. Merge them.

        Handles both directions: A's canonical is B's alias, AND B's canonical is A's alias.
        Skips entities that have a FAMILY_OF relation between them (different generations).
        When multiple candidates share the same alias, picks the best one without FAMILY_OF.
        """
        merges = 0
        entities = list(graph.entities.values())
        for absorbed in entities:
            if absorbed.entity_id not in graph.entities:
                continue

            # Collect ALL survivors whose aliases contain absorbed's canonical_name
            candidates: list[str] = []
            for other in graph.entities.values():
                if other.entity_id == absorbed.entity_id:
                    continue
                if other.entity_type != absorbed.entity_type:
                    continue
                if absorbed.canonical_name in other.aliases:
                    candidates.append(other.entity_id)

            if not candidates:
                continue

            # Filter out candidates with FAMILY_OF relation to absorbed
            def _has_family(a_id: str, b_id: str) -> bool:
                for rel in graph.relations.values():
                    if rel.relation_type == "FAMILY_OF":
                        ids = {rel.source_entity_id, rel.target_entity_id}
                        if a_id in ids and b_id in ids:
                            return True
                return False

            valid = [c for c in candidates if not _has_family(absorbed.entity_id, c)]
            if not valid:
                continue

            # Pick best survivor (highest mention_count)
            survivor_id = max(valid, key=lambda cid: graph.entities[cid].mention_count)
            survivor = graph.entities[survivor_id]

            if survivor.mention_count >= absorbed.mention_count:
                self._merge_entity_into(
                    survivor=survivor, absorbed=absorbed,
                    graph=graph, entity_id_by_alias=entity_id_by_alias,
                )
            else:
                self._merge_entity_into(
                    survivor=absorbed, absorbed=survivor,
                    graph=graph, entity_id_by_alias=entity_id_by_alias,
                )
            merges += 1
        return merges

    def _emit_progress(self, **payload: dict) -> None:
        if self.progress_callback is not None:
            self.progress_callback(payload)

    def _resolve_entities(
        self,
        chunk: BookChunk,
        graph: TemporalContextGraph,
        entity_id_by_alias: dict[str, str],
        llm_episode_extraction: llm_extraction.EpisodeGraphExtraction | None = None,
    ) -> list[EntityNode]:
        extraction_candidates = self._entity_candidates_from_extraction(chunk, llm_episode_extraction)
        resolved: list[EntityNode] = []
        for raw_name, raw_type, aliases, resolution_hint, evidence, confidence, resolution_strategy in extraction_candidates:
            canonical_name = _normalize_entity_name(raw_name)
            aliases = sorted({*aliases, *(_entity_aliases(canonical_name))})
            alias_keys = {_slugify(alias) for alias in aliases}
            entity_id = None
            fuzzy_matched = False
            if resolution_hint:
                entity_id = entity_id_by_alias.get(_slugify(resolution_hint))
            if entity_id is None:
                for alias_key in alias_keys:
                    entity_id = entity_id_by_alias.get(alias_key)
                    if entity_id:
                        break
            if entity_id is None:
                fuzzy_keys: set[str] = set()
                for alias in aliases:
                    fuzzy_keys.update(_normalize_name_for_matching(alias))
                fuzzy_keys.update(_normalize_name_for_matching(canonical_name))
                best_fuzzy_match: tuple[str, int] | None = None
                for fuzzy_key in fuzzy_keys:
                    candidate_id = entity_id_by_alias.get(fuzzy_key)
                    if candidate_id and candidate_id in graph.entities:
                        mc = graph.entities[candidate_id].mention_count
                        if best_fuzzy_match is None or mc > best_fuzzy_match[1]:
                            best_fuzzy_match = (candidate_id, mc)
                if best_fuzzy_match is not None and best_fuzzy_match[1] >= 3:
                    entity_id = best_fuzzy_match[0]
                    fuzzy_matched = True
            if entity_id is None:
                entity_id = f"entity_{_slugify(canonical_name)}"
                if entity_id in graph.entities:
                    relation_index = len(graph.entities) + 1
                    entity_id = f"{entity_id}_{relation_index:03d}"
                entity = EntityNode(
                    entity_id=entity_id,
                    canonical_name=canonical_name,
                    aliases=aliases,
                    entity_type=_infer_entity_type(canonical_name, raw_type, chunk),  # type: ignore[arg-type]
                    metadata={
                        "resolution_strategy": resolution_strategy,
                        "resolution_hint": resolution_hint,
                        "last_evidence": evidence,
                        "last_confidence": confidence,
                    },
                )
                graph.entities[entity_id] = entity
            else:
                entity = graph.entities[entity_id]
                for alias in aliases:
                    if alias not in entity.aliases:
                        entity.aliases.append(alias)
                if canonical_name not in entity.aliases and canonical_name != entity.canonical_name:
                    entity.aliases.append(canonical_name)
                entity.metadata["resolution_strategy"] = "fuzzy-prefix-match" if fuzzy_matched else resolution_strategy
                if resolution_hint:
                    entity.metadata["resolution_hint"] = resolution_hint
                if evidence:
                    entity.metadata["last_evidence"] = evidence
                entity.metadata["last_confidence"] = confidence
            for alias_key in alias_keys:
                entity_id_by_alias[alias_key] = entity.entity_id
            resolved.append(entity)
        deduped = {entity.entity_id: entity for entity in resolved}
        return list(deduped.values())

    def _entity_candidates_from_extraction(
        self,
        chunk: BookChunk,
        llm_episode_extraction: llm_extraction.EpisodeGraphExtraction | None,
    ) -> list[tuple[str, str, list[str], str, str, float, str]]:
        if llm_episode_extraction and llm_episode_extraction.entities:
            rows: list[tuple[str, str, list[str], str, str, float, str]] = []
            for item in llm_episode_extraction.entities:
                aliases = [alias for alias in item.aliases if alias.strip()]
                rows.append(
                    (
                        item.canonical_name,
                        item.entity_type,
                        aliases,
                        item.resolution_hint,
                        item.evidence,
                        item.confidence,
                        "llm-assisted",
                    )
                )
            return rows
        return []

    def _extract_and_resolve_relations(
        self,
        *,
        chunk: BookChunk,
        episode: EpisodeNode,
        entity_nodes: list[EntityNode],
        graph: TemporalContextGraph,
        now: str,
        active_relation_by_signature: dict[str, str],
        active_state_relation_by_key: dict[str, str],
        relation_version_counter: Counter[str],
        llm_episode_extraction: llm_extraction.EpisodeGraphExtraction | None = None,
    ) -> list[str]:
        relation_ids: list[str] = []
        if llm_episode_extraction and llm_episode_extraction.facts:
            for fact_candidate in llm_episode_extraction.facts:
                source_entity = self._match_entity_by_name(fact_candidate.source, entity_nodes)
                target_entity = self._match_entity_by_name(fact_candidate.target, entity_nodes)
                if source_entity is None or target_entity is None or source_entity.entity_id == target_entity.entity_id:
                    continue
                normalized_fact = normalize_fact_candidate(
                    fact=FactCandidate(
                        subject=source_entity.canonical_name,
                        predicate=fact_candidate.relation_type,
                        object=target_entity.canonical_name,
                        relation_family=fact_candidate.state_family,
                        fact_text=fact_candidate.fact,
                        certainty=fact_candidate.confidence,
                        tvalid_start_chapter=chunk.chapter_index,
                        tvalid_start_paragraph=chunk.paragraph_index,
                        evidence_episode_ids=[episode.episode_id],
                        evidence_spans=[
                            {
                                "chunk_id": chunk.chunk_id,
                                "chapter_index": chunk.chapter_index,
                                "paragraph_index": chunk.paragraph_index,
                                "evidence_text": fact_candidate.evidence or fact_candidate.fact,
                            }
                        ],
                        metadata={
                            "raw_relation_type": fact_candidate.relation_type,
                            "raw_state_family": fact_candidate.state_family,
                        },
                    ),
                    raw_relation_type=fact_candidate.relation_type,
                    raw_state_family=fact_candidate.state_family,
                    raw_directionality=fact_candidate.directionality,
                    target_entity_type=target_entity.entity_type,
                )
                relation_ids.extend(
                    self._upsert_relation_edge(
                        chunk=chunk,
                        episode=episode,
                        graph=graph,
                        now=now,
                        active_relation_by_signature=active_relation_by_signature,
                        active_state_relation_by_key=active_state_relation_by_key,
                        relation_version_counter=relation_version_counter,
                        source_entity=source_entity,
                        target_entity=target_entity,
                        relation_type=normalized_fact.relation_type,
                        state_family=normalized_fact.state_family,
                        directionality=normalized_fact.directionality,
                        fact_text=normalized_fact.fact.fact_text,
                        evidence_text=fact_candidate.evidence or fact_candidate.fact,
                        extraction_mode="llm-assisted",
                        confidence=fact_candidate.confidence,
                        relation_family=normalized_fact.relation_family,
                        state_slot=normalized_fact.state_slot,
                        normalization_notes=normalized_fact.normalization_notes,
                        raw_relation_type=fact_candidate.relation_type,
                        raw_state_family=fact_candidate.state_family,
                    )
                )
        return sorted(set(relation_ids))

    def _upsert_relation_edge(
        self,
        *,
        chunk: BookChunk,
        episode: EpisodeNode,
        graph: TemporalContextGraph,
        now: str,
        active_relation_by_signature: dict[str, str],
        active_state_relation_by_key: dict[str, str],
        relation_version_counter: Counter[str],
        source_entity: EntityNode,
        target_entity: EntityNode,
        relation_type: str,
        state_family: str,
        directionality: RelationDirectionality,
        fact_text: str,
        evidence_text: str,
        extraction_mode: str,
        confidence: float,
        relation_family: str,
        state_slot: str | None,
        normalization_notes: list[str],
        raw_relation_type: str,
        raw_state_family: str,
    ) -> list[str]:
        signature = _fact_signature(
            relation_type=relation_type,
            state_family=state_family,
            source_entity_id=source_entity.entity_id,
            target_entity_id=target_entity.entity_id,
            directionality=directionality,
        )
        existing_edge_id = active_relation_by_signature.get(signature)
        if existing_edge_id:
            edge = graph.relations[existing_edge_id]
            edge.weight += 1.0
            if episode.episode_id not in edge.episode_ids:
                edge.episode_ids.append(episode.episode_id)
            edge.provenance.append(_build_provenance(chunk, "relation", {"sentence": evidence_text}))
            edge.metadata["last_seen_episode_id"] = episode.episode_id
            edge.metadata["extraction_mode"] = extraction_mode
            edge.metadata["confidence"] = max(float(edge.metadata.get("confidence", 0.0)), confidence)
            return [edge.edge_id]

        state_key = build_state_slot(
            state_family=state_family,
            source_entity_id=source_entity.entity_id,
            directionality=directionality,
            target_entity_id=target_entity.entity_id,
            state_slot=state_slot,
        )
        superseded_edges: list[str] = []
        if state_key:
            active_state_edge_id = active_state_relation_by_key.get(state_key)
            if active_state_edge_id and active_state_edge_id in graph.relations:
                active_edge = graph.relations[active_state_edge_id]
                if active_edge.target_entity_id != target_entity.entity_id:
                    active_edge.status = "invalidated"
                    active_edge.invalid_at_chapter = chunk.chapter_index
                    active_edge.invalid_at_paragraph = chunk.paragraph_index
                    active_edge.expired_at = now
                    active_edge.invalidated_by_edge_id = "pending"
                    active_edge.metadata["invalidated_by_episode_id"] = episode.episode_id
                    superseded_edges.append(active_edge.edge_id)

        base_signature_slug = _slugify(signature)
        relation_version_counter[base_signature_slug] += 1
        version = relation_version_counter[base_signature_slug]
        edge_id = f"edge_{base_signature_slug}_v{version:03d}"
        reference_time = f"narrative://{chunk.book_id}/c{chunk.chapter_index:03d}/p{chunk.paragraph_index:03d}"
        edge = RelationEdge(
            edge_id=edge_id,
            source_entity_id=source_entity.entity_id,
            target_entity_id=target_entity.entity_id,
            relation_type=relation_type,
            state_family=state_family,
            directionality=directionality,
            fact=fact_text,
            fact_signature=signature,
            weight=1.0,
            status="active",
            valid_at_chapter=chunk.chapter_index,
            valid_at_paragraph=chunk.paragraph_index,
            created_at=now,
            reference_time=reference_time,
            supersedes_edge_ids=superseded_edges,
            episode_ids=[episode.episode_id],
            metadata={
                "chapter_id": chunk.chapter_id,
                "paragraph_id": chunk.paragraph_id,
                "sentence_excerpt": _excerpt(evidence_text, limit=240),
                "state_key": state_key,
                "state_slot": state_slot,
                "relation_family": relation_family,
                "raw_relation_type": raw_relation_type,
                "raw_state_family": raw_state_family,
                "normalization_notes": normalization_notes,
                "extraction_mode": extraction_mode,
                "confidence": confidence,
            },
            provenance=[
                _build_provenance(
                    chunk,
                    "relation",
                    {
                        "sentence": evidence_text,
                        "reference_time": reference_time,
                        "episode_id": episode.episode_id,
                        "evidence_text": evidence_text,
                    },
                )
            ],
        )
        graph.relations[edge_id] = edge
        active_relation_by_signature[signature] = edge_id
        if state_key:
            active_state_relation_by_key[state_key] = edge_id
        for superseded_edge_id in superseded_edges:
            graph.relations[superseded_edge_id].invalidated_by_edge_id = edge_id
        return [edge_id]

    def _match_entity_by_name(self, name: str, entity_nodes: list[EntityNode]) -> EntityNode | None:
        normalized = _normalize_entity_name(name)
        if not normalized:
            return None
        lowered = normalized.lower()
        for entity in entity_nodes:
            if entity.canonical_name.lower() == lowered:
                return entity
            if any(alias.lower() == lowered for alias in entity.aliases):
                return entity
        for entity in entity_nodes:
            forms = {entity.canonical_name.lower(), *(alias.lower() for alias in entity.aliases)}
            if any(lowered in form or form in lowered for form in forms if form):
                return entity
        return None

    def _build_dependency_edges(
        self,
        graph: TemporalContextGraph,
        chapter_episode_ids: dict[int, list[str]],
    ) -> None:
        """Build narrative dependency edges between episodes.

        Rules (zero LLM cost):
        1. Sequential adjacency: episode N+1 depends_on episode N (same chapter)
        2. Entity overlap: if episode B shares ≥2 entities with episode A and they
           are in the same chapter, B depends_on A (potential causal link)
        """
        episodes_by_chapter: dict[int, list[EpisodeNode]] = defaultdict(list)
        for ep in graph.episodes.values():
            episodes_by_chapter[ep.chapter_index].append(ep)

        for chapter_index, eps in episodes_by_chapter.items():
            eps.sort(key=lambda e: e.paragraph_index)
            for i in range(len(eps)):
                current = eps[i]
                # Rule 1: sequential adjacency
                if i > 0:
                    prev = eps[i - 1]
                    if prev.episode_id not in current.depends_on:
                        current.depends_on.append(prev.episode_id)
                    if current.episode_id not in prev.depended_by:
                        prev.depended_by.append(current.episode_id)
                # Rule 2: entity overlap within chapter
                if i > 0:
                    for j in range(i - 1, max(i - 5, -1), -1):
                        earlier = eps[j]
                        overlap = set(current.entity_ids) & set(earlier.entity_ids)
                        if len(overlap) >= 2 and earlier.episode_id not in current.depends_on:
                            current.depends_on.append(earlier.episode_id)
                            if current.episode_id not in earlier.depended_by:
                                earlier.depended_by.append(current.episode_id)

    def _entity_summary(self, entity: EntityNode, chapter_span: list[int], graph: TemporalContextGraph) -> str:
        if not chapter_span:
            return f"{entity.canonical_name} appears in the current book graph."

        eid = entity.entity_id
        ch_start, ch_end = chapter_span[0], chapter_span[-1]
        ch_range = f"ch{ch_start}" if ch_start == ch_end else f"ch{ch_start}-{ch_end}"

        # --- Identity: extract who this entity IS from FAMILY_OF relations ---
        identity_parts: list[str] = []
        for rel in graph.relations.values():
            if rel.relation_type != "FAMILY_OF" or rel.status != "active":
                continue
            # Incoming FAMILY_OF: "A 是 B 的父亲" → this entity is B's father
            if rel.source_entity_id == eid:
                tgt = graph.entities.get(rel.target_entity_id)
                if tgt:
                    identity_parts.append(rel.fact)
            # Outgoing FAMILY_OF: "A 是 B 的父亲" and this entity IS B → identity
            elif rel.target_entity_id == eid:
                src = graph.entities.get(rel.source_entity_id)
                if src:
                    identity_parts.append(rel.fact)

        # --- Sort identity by specificity (prefer facts that mention more specific names) ---
        identity_parts.sort(key=lambda f: len(f), reverse=True)

        # Build the summary
        name = entity.canonical_name
        if identity_parts:
            # Pick 2-3 most specific identity facts
            id_text = "；".join(identity_parts[:3])
            summary = f"{name} — {id_text}。出现在 {ch_range}，共 {entity.mention_count} 次提及。"
        else:
            # No family info: use entity type + chapter location
            type_cn = {
                "character": "角色", "location": "地点", "artifact": "物品",
                "group": "团体/组织", "concept": "概念/主题", "unknown": "未知",
            }.get(entity.entity_type, entity.entity_type)
            summary = f"{name}（{type_cn}）。出现在 {ch_range}，共 {entity.mention_count} 次提及。"

        # --- For mc=1 fragments: note that this may be a fragment ---
        if entity.mention_count == 1 and entity.entity_type == "character":
            summary += " 仅出现一次，可能为零散提及。"

        return summary

    def _build_communities(
        self,
        graph: TemporalContextGraph,
        chapter_entities: dict[int, set[str]],
        chapter_episode_ids: dict[int, list[str]],
        chapter_provenance: dict[int, list[GraphProvenance]],
    ) -> dict[str, CommunityNode]:
        return self._build_communities_from_seeds(
            graph=graph,
            chapter_entities=chapter_entities,
            chapter_episode_ids=chapter_episode_ids,
            chapter_provenance=chapter_provenance,
            seed_entity_ids=None,  # None = full scan
        )

    def _build_communities_from_seeds(
        self,
        graph: TemporalContextGraph,
        chapter_entities: dict[int, set[str]],
        chapter_episode_ids: dict[int, list[str]],
        chapter_provenance: dict[int, list[GraphProvenance]],
        seed_entity_ids: set[str] | None = None,
    ) -> dict[str, CommunityNode]:
        """Build (or update) communities. If seed_entity_ids is given, only explore
        from those entities — connecting them to existing communities or forming new ones.
        Otherwise, do a full BFS over all entities."""
        adjacency: dict[str, set[str]] = defaultdict(set)
        relation_ids_by_entity: dict[str, set[str]] = defaultdict(set)
        for edge in graph.relations.values():
            adjacency[edge.source_entity_id].add(edge.target_entity_id)
            adjacency[edge.target_entity_id].add(edge.source_entity_id)
            relation_ids_by_entity[edge.source_entity_id].add(edge.edge_id)
            relation_ids_by_entity[edge.target_entity_id].add(edge.edge_id)

        communities: dict[str, CommunityNode] = {}
        visited: set[str] = set()
        component_index = len(graph.communities) + 1

        # In incremental mode: mark existing community entities as visited
        # so they're not re-explored; new entities will attach to them via BFS.
        if seed_entity_ids is not None:
            for c in graph.communities.values():
                visited.update(c.entity_ids)

        seeds = sorted(seed_entity_ids) if seed_entity_ids is not None else sorted(graph.entities)

        for entity_id in seeds:
            if entity_id in visited:
                continue
            stack = [entity_id]
            component: set[str] = set()
            while stack:
                current = stack.pop()
                if current in visited:
                    continue
                visited.add(current)
                component.add(current)
                stack.extend(adjacency[current] - visited)

            # If the BFS touched an existing community, merge component into it
            touched_community_ids: set[str] = set()
            for cid, c in graph.communities.items():
                if component.intersection(c.entity_ids):
                    touched_community_ids.add(cid)

            if touched_community_ids:
                # Merge component into the first touched community, extend its span
                survivor_id = min(touched_community_ids)
                survivor = graph.communities[survivor_id]
                for eid in component:
                    if eid not in survivor.entity_ids:
                        survivor.entity_ids.append(eid)
                # Merge other touched communities into survivor
                for cid in touched_community_ids:
                    if cid == survivor_id:
                        continue
                    absorbed = graph.communities.pop(cid)
                    for eid in absorbed.entity_ids:
                        if eid not in survivor.entity_ids:
                            survivor.entity_ids.append(eid)
                    for epid in absorbed.episode_ids:
                        if epid not in survivor.episode_ids:
                            survivor.episode_ids.append(epid)
                    for rid in absorbed.relation_ids:
                        if rid not in survivor.relation_ids:
                            survivor.relation_ids.append(rid)
                    survivor.metadata["entity_count"] = len(survivor.entity_ids)
                    # Re-point episodes to survivor
                    for epid in absorbed.episode_ids:
                        ep = graph.episodes.get(epid)
                        if ep and cid in ep.community_ids:
                            ep.community_ids.remove(cid)
                            if survivor_id not in ep.community_ids:
                                ep.community_ids.append(survivor_id)

                # Refresh survivor metadata
                chapters = sorted(
                    ci for ci, eids in chapter_entities.items()
                    if set(eids).intersection(survivor.entity_ids)
                )
                if chapters:
                    survivor.chapter_start = min(survivor.chapter_start, chapters[0])
                    survivor.chapter_end = max(survivor.chapter_end, chapters[-1])
                survivor.relation_ids = sorted(
                    {rid for eid in survivor.entity_ids for rid in relation_ids_by_entity.get(eid, set())}
                )
                survivor.metadata["entity_count"] = len(survivor.entity_ids)
                # Update label
                top = sorted(
                    survivor.entity_ids,
                    key=lambda eid: graph.entities[eid].mention_count,
                    reverse=True,
                )[:3]
                survivor.label = "/".join(graph.entities[eid].canonical_name for eid in top)
                for epid in survivor.episode_ids:
                    ep = graph.episodes.get(epid)
                    if ep and survivor_id not in ep.community_ids:
                        ep.community_ids.append(survivor_id)
                continue

            # New standalone community
            chapters = sorted(
                ci for ci, eids in chapter_entities.items()
                if eids.intersection(component)
            )
            episode_ids = sorted({
                epid
                for ci in chapters
                for epid in chapter_episode_ids[ci]
                if set(graph.episodes[epid].entity_ids).intersection(component)
            })
            relation_ids = sorted({rid for eid in component for rid in relation_ids_by_entity.get(eid, set())})
            label_names = [
                graph.entities[c].canonical_name
                for c in sorted(component, key=lambda eid: graph.entities[eid].mention_count, reverse=True)[:3]
            ]
            cid = f"community_{component_index:03d}"
            communities[cid] = CommunityNode(
                community_id=cid,
                label="/".join(label_names) or cid,
                community_name=f"community_{component_index}",
                keywords=sorted({n.lower() for n in label_names if n.strip()}),
                retrieval_text=" ".join(["community", *label_names, cid]).strip(),
                local_summary=f"Community {component_index} links {', '.join(label_names) or 'entities'} through {len(relation_ids)} facts.",
                summary=f"Community {component_index} links {', '.join(label_names) or 'entities'} through {len(relation_ids)} facts.",
                entity_ids=sorted(component),
                episode_ids=episode_ids,
                relation_ids=relation_ids,
                chapter_start=chapters[0] if chapters else 0,
                chapter_end=chapters[-1] if chapters else 0,
                metadata={"entity_count": len(component), "dominant_entities": label_names},
                provenance=[item for ci in chapters for item in chapter_provenance[ci][:2]],
            )
            for epid in episode_ids:
                graph.episodes[epid].community_ids.append(cid)
            component_index += 1

        return communities

    def _build_sagas(
        self,
        graph: TemporalContextGraph,
        chapter_entities: dict[int, set[str]],
        chapter_episode_ids: dict[int, list[str]],
        chapter_provenance: dict[int, list[GraphProvenance]],
    ) -> dict[str, SagaNode]:
        return self._build_sagas_from_chapter(
            graph=graph,
            chapter_entities=chapter_entities,
            chapter_episode_ids=chapter_episode_ids,
            chapter_provenance=chapter_provenance,
            start_chapter=None,  # None = full scan
        )

    def _build_sagas_from_chapter(
        self,
        graph: TemporalContextGraph,
        chapter_entities: dict[int, set[str]],
        chapter_episode_ids: dict[int, list[str]],
        chapter_provenance: dict[int, list[GraphProvenance]],
        start_chapter: int | None = None,
    ) -> dict[str, SagaNode]:
        """Build (or extend) sagas. If start_chapter is given, only process from that
        chapter onward, extending existing sagas. Otherwise, do a full scan."""
        if not chapter_episode_ids:
            return {}

        sagas: dict[str, SagaNode] = {}
        sorted_chapters = sorted(chapter_episode_ids)

        if start_chapter is not None:
            # Incremental: keep existing sagas, extend from start_chapter
            sagas = dict(graph.sagas)
            # Find the last saga and its chapter range
            last_saga = None
            for s in sorted(sagas.values(), key=lambda s: s.chapter_end):
                last_saga = s
            if last_saga is not None:
                current_group = list(range(last_saga.chapter_start, last_saga.chapter_end + 1))
            else:
                current_group = [sorted_chapters[0]] if sorted_chapters else []
            process_chapters = [c for c in sorted_chapters if c >= start_chapter]
        else:
            # Full build: start from scratch
            sagas.clear()
            graph.sagas.clear()
            saga_groups: list[list[int]] = []
            current_group: list[int] = []
            process_chapters = sorted_chapters
            for ci in sorted_chapters:
                if not current_group:
                    current_group = [ci]
                    continue
                prev = chapter_entities.get(current_group[-1], set())
                cur = chapter_entities.get(ci, set())
                if prev.intersection(cur):
                    current_group.append(ci)
                else:
                    saga_groups.append(current_group)
                    current_group = [ci]
            if current_group:
                saga_groups.append(current_group)
            # Build sagas from groups
            for index, chapters in enumerate(saga_groups, start=1):
                saga = self._make_saga_node(
                    graph, chapters, chapter_entities, chapter_episode_ids, chapter_provenance, index,
                )
                sagas[saga.saga_id] = saga
            return sagas

        # Incremental mode: extend or create sagas
        if not current_group:
            current_group = [process_chapters[0]] if process_chapters else []

        for ci in process_chapters:
            prev_entities = chapter_entities.get(current_group[-1], set())
            cur_entities = chapter_entities.get(ci, set())
            if prev_entities.intersection(cur_entities):
                if ci not in current_group:
                    current_group.append(ci)
            else:
                # Finalize current group as a saga
                existing = [s for s in sagas.values() if s.chapter_start == current_group[0] and s.chapter_end == current_group[-1]]
                if not existing:
                    saga = self._make_saga_node(
                        graph, current_group, chapter_entities, chapter_episode_ids, chapter_provenance,
                        len(sagas) + 1,
                    )
                    sagas[saga.saga_id] = saga
                current_group = [ci]

        if current_group:
            existing = [s for s in sagas.values() if s.chapter_start == current_group[0] and s.chapter_end == current_group[-1]]
            if not existing:
                saga = self._make_saga_node(
                    graph, current_group, chapter_entities, chapter_episode_ids, chapter_provenance,
                    len(sagas) + 1,
                )
                sagas[saga.saga_id] = saga

        return sagas

    def _make_saga_node(
        self,
        graph: TemporalContextGraph,
        chapters: list[int],
        chapter_entities: dict[int, set[str]],
        chapter_episode_ids: dict[int, list[str]],
        chapter_provenance: dict[int, list[GraphProvenance]],
        index: int,
    ) -> SagaNode:
        episode_ids = [eid for ci in chapters for eid in chapter_episode_ids[ci]]
        entity_counter = Counter(
            eid for ci in chapters for eid in chapter_entities.get(ci, set())
        )
        dominant = [eid for eid, _ in entity_counter.most_common(4)]
        label_parts = [graph.entities[eid].canonical_name for eid in dominant[:2]]
        relation_ids = [
            r.edge_id for r in graph.relations.values()
            if r.valid_at_chapter >= chapters[0] and r.valid_at_chapter <= chapters[-1]
        ]
        summary = (
            f"Chapters {chapters[0]}-{chapters[-1]} track "
            f"{', '.join(label_parts) if label_parts else 'the current narrative thread'} "
            f"through {len(episode_ids)} episodes and {len(relation_ids)} facts."
        )
        sid = f"saga_{index:03d}"
        saga = SagaNode(
            saga_id=sid,
            label=" / ".join(label_parts) or f"chapters_{chapters[0]}_{chapters[-1]}",
            arc_type="chapter_span_arc",
            key_entities=label_parts,
            retrieval_text=" ".join(["saga", *label_parts, summary]).strip(),
            episode_ids=episode_ids,
            entity_ids=dominant,
            relation_ids=relation_ids,
            chapter_start=chapters[0],
            chapter_end=chapters[-1],
            chapter_range=(chapters[0], chapters[-1]),
            summary=summary,
            metadata={"chapter_count": len(chapters), "dominant_entities": dominant},
            provenance=[item for ci in chapters for item in chapter_provenance[ci][:2]],
        )
        for epid in episode_ids:
            ep = graph.episodes.get(epid)
            if ep is not None and sid not in ep.saga_ids:
                ep.saga_ids.append(sid)
        return saga

    def _build_chapter_timeline(
        self,
        graph: TemporalContextGraph,
        chapter_episode_ids: dict[int, list[str]],
        chapter_entities: dict[int, set[str]],
        chapter_relation_ids: dict[int, set[str]],
        chapter_active_relation_ids: dict[int, set[str]],
        chapter_invalidated_relation_ids: dict[int, set[str]],
        chapter_provenance: dict[int, list[GraphProvenance]],
        chapter_paragraph_count: Counter[int],
    ) -> list[ChapterTimelineEntry]:
        timeline: list[ChapterTimelineEntry] = []
        saga_map: dict[int, list[str]] = defaultdict(list)
        community_map: dict[int, list[str]] = defaultdict(list)
        for saga in graph.sagas.values():
            for chapter_index in range(saga.chapter_start, saga.chapter_end + 1):
                saga_map[chapter_index].append(saga.saga_id)
        for community in graph.communities.values():
            for chapter_index in range(community.chapter_start, community.chapter_end + 1):
                community_map[chapter_index].append(community.community_id)

        for chapter_index in sorted(chapter_episode_ids):
            chapter_id = graph.episodes[chapter_episode_ids[chapter_index][0]].chapter_id
            title = graph.chapters[f"chapter_{chapter_index:03d}"].title
            entity_names = [
                graph.entities[entity_id].canonical_name
                for entity_id in sorted(
                    chapter_entities.get(chapter_index, set()),
                    key=lambda item: graph.entities[item].mention_count,
                    reverse=True,
                )[:3]
            ]
            timeline.append(
                ChapterTimelineEntry(
                    chapter_id=chapter_id,
                    chapter_index=chapter_index,
                    title=title,
                    episode_ids=chapter_episode_ids[chapter_index],
                    entity_ids=sorted(chapter_entities.get(chapter_index, set())),
                    relation_ids=sorted(chapter_relation_ids.get(chapter_index, set())),
                    active_relation_ids=sorted(chapter_active_relation_ids.get(chapter_index, set())),
                    invalidated_relation_ids=sorted(chapter_invalidated_relation_ids.get(chapter_index, set())),
                    community_ids=community_map.get(chapter_index, []),
                    saga_ids=saga_map.get(chapter_index, []),
                    spoiler_level=max(
                        graph.episodes[episode_id].spoiler_level for episode_id in chapter_episode_ids[chapter_index]
                    ),
                    paragraph_count=chapter_paragraph_count[chapter_index],
                    summary=(
                        f"Chapter {chapter_index} centers on "
                        f"{', '.join(entity_names) if entity_names else 'the local narrative state'}."
                    ),
                    metadata={"dominant_entities": entity_names},
                    provenance=chapter_provenance[chapter_index][:4],
                )
            )
        return timeline

    def _attach_chapter_collections(self, graph: TemporalContextGraph) -> None:
        for timeline_entry in graph.chapter_timeline:
            chapter = graph.chapters.get(f"chapter_{timeline_entry.chapter_index:03d}")
            if chapter is None:
                continue
            chapter.community_ids = timeline_entry.community_ids
            chapter.saga_ids = timeline_entry.saga_ids
            chapter.active_relation_ids = timeline_entry.active_relation_ids
            chapter.invalidated_relation_ids = timeline_entry.invalidated_relation_ids
            chapter.metadata["timeline_summary"] = timeline_entry.summary
            for relation_id in timeline_entry.relation_ids:
                if relation_id not in chapter.relation_ids:
                    chapter.relation_ids.append(relation_id)

    def _extract_episode_with_llm(
        self,
        *,
        chunk: BookChunk,
        graph: TemporalContextGraph,
    ) -> llm_extraction.EpisodeGraphExtraction | None:
        if self.extractor_runtime is None:
            return None

        known_entities = [
            llm_extraction.KnownEntityCandidate(
                entity_id=entity.entity_id,
                canonical_name=entity.canonical_name,
                entity_type=entity.entity_type,
                aliases=entity.aliases,
                mention_count=entity.mention_count,
                last_seen_chapter=entity.last_seen_chapter,
                last_seen_paragraph=entity.last_seen_paragraph,
            )
            for entity in sorted(graph.entities.values(), key=lambda item: item.mention_count, reverse=True)
            if entity.last_seen_chapter < chunk.chapter_index
            or (
                entity.last_seen_chapter == chunk.chapter_index
                and entity.last_seen_paragraph < chunk.paragraph_index
            )
        ]
        recent_episode_contexts = [
            episode.text for episode in sorted(graph.episodes.values(), key=lambda item: item.episode_index)[-3:]
        ]
        try:
            graph.metadata["llm_calls"] = int(graph.metadata.get("llm_calls", 0)) + 1
            self._emit_progress(
                stage="llm-request-dispatched",
                title="LLM entity/fact resolution",
                message=f"Dispatching entity/fact extraction prompt for {chunk.chunk_id}.",
                current_snippet_id=chunk.chunk_id,
                current_chapter_index=chunk.chapter_index,
                current_paragraph_index=chunk.paragraph_index,
                details={
                    "phase": "llm-request-dispatched",
                    "provider": self.extractor_runtime.provider_label,
                    "source_paragraph_indices": chunk.metadata.get("source_paragraph_indices", []),
                    "source_paragraph_count": chunk.metadata.get("source_paragraph_count", 1),
                    "packet_token_count": chunk.metadata.get("packet_token_count", len(chunk.text)),
                    "is_merged_packet": chunk.metadata.get("is_merged_packet", False),
                },
            )
            extraction = llm_extraction.extract_episode_graph_with_llm(
                runtime=self.extractor_runtime,
                chunk=chunk,
                known_entities=known_entities,
                recent_episode_contexts=recent_episode_contexts,
            )
            self._emit_progress(
                stage="llm-response-received",
                title="LLM response received",
                message=(
                    f"Received LLM extraction for {chunk.chunk_id}: "
                    f"{len(extraction.entities)} entities, {len(extraction.facts)} facts."
                ),
                current_snippet_id=chunk.chunk_id,
                current_chapter_index=chunk.chapter_index,
                current_paragraph_index=chunk.paragraph_index,
                details={
                    "phase": "llm-response-received",
                    "entity_candidates": len(extraction.entities),
                    "fact_candidates": len(extraction.facts),
                    "source_paragraph_indices": chunk.metadata.get("source_paragraph_indices", []),
                    "source_paragraph_count": chunk.metadata.get("source_paragraph_count", 1),
                    "packet_token_count": chunk.metadata.get("packet_token_count", len(chunk.text)),
                    "is_merged_packet": chunk.metadata.get("is_merged_packet", False),
                },
            )
            return extraction
        except Exception as exc:
            graph.metadata.setdefault("llm_extraction_warnings", [])
            graph.metadata["llm_extraction_warnings"].append(
                {
                    "chunk_id": chunk.chunk_id,
                    "chapter_index": chunk.chapter_index,
                    "paragraph_index": chunk.paragraph_index,
                    "reason": str(exc),
                }
            )
            if self.strict_llm_extraction:
                raise RuntimeError(f"strict llm extraction failed for {chunk.chunk_id}: {exc}") from exc
            self._emit_progress(
                stage="llm-request-failed",
                title="LLM extraction failed",
                message=f"LLM extraction failed for {chunk.chunk_id}; skipping episode.",
                current_snippet_id=chunk.chunk_id,
                current_chapter_index=chunk.chapter_index,
                current_paragraph_index=chunk.paragraph_index,
                details={
                    "phase": "llm-request-failed",
                    "error": str(exc),
                    "source_paragraph_indices": chunk.metadata.get("source_paragraph_indices", []),
                    "source_paragraph_count": chunk.metadata.get("source_paragraph_count", 1),
                    "packet_token_count": chunk.metadata.get("packet_token_count", len(chunk.text)),
                    "is_merged_packet": chunk.metadata.get("is_merged_packet", False),
                },
            )
            return None

    def _build_family_tree_context(self, graph: TemporalContextGraph) -> list[str]:
        """Extract known FAMILY_OF relations for the system prompt."""
        lines: list[str] = []
        for rel in graph.relations.values():
            if rel.relation_type == "FAMILY_OF" and rel.status == "active":
                src = graph.entities.get(rel.source_entity_id)
                tgt = graph.entities.get(rel.target_entity_id)
                if src and tgt:
                    lines.append(f"{src.canonical_name} --[FAMILY_OF]--> {tgt.canonical_name} ({rel.fact})")
        return lines

    def _build_character_description_cache(self, graph: TemporalContextGraph) -> list[str]:
        """Build behavior-rich descriptions for character disambiguation (LINK-KG style).

        Each description includes identity (who they ARE), behavior (what they DO),
        and chapter span — so the LLM can distinguish characters even when names are
        ambiguous in the current text.
        """
        gen_map = self._infer_generation_map(graph)
        lines: list[str] = []
        for entity in sorted(graph.entities.values(), key=lambda e: e.mention_count, reverse=True):
            if entity.mention_count < 3:
                continue
            if entity.entity_type != "character":
                continue
            ch_span = entity.metadata.get("chapter_span", [])
            ch_range = f"ch{ch_span[0]}" if ch_span and len(ch_span) == 1 else f"ch{ch_span[0]}-{ch_span[-1]}" if ch_span else "?"

            # Identity from FAMILY_OF
            identity: list[str] = []
            # Behavioral facts from interactions
            behaviors: list[str] = []
            for rel in graph.relations.values():
                if rel.status != "active":
                    continue
                if rel.source_entity_id == entity.entity_id:
                    other = graph.entities.get(rel.target_entity_id)
                    if not other:
                        continue
                    if rel.relation_type == "FAMILY_OF":
                        identity.append(rel.fact)
                    elif rel.relation_type in ("SPOKE_WITH", "CARES_ABOUT", "CONFLICTS_WITH"):
                        behaviors.append(f"{rel.relation_type} {other.canonical_name}: {rel.fact}")
                elif rel.target_entity_id == entity.entity_id:
                    other = graph.entities.get(rel.source_entity_id)
                    if not other:
                        continue
                    if rel.relation_type == "FAMILY_OF":
                        identity.append(rel.fact)
                    elif rel.relation_type in ("SPOKE_WITH", "CARES_ABOUT", "CONFLICTS_WITH"):
                        behaviors.append(f"{other.canonical_name} {rel.relation_type}: {rel.fact}")

            # Build compact description
            parts = [f"-{entity.canonical_name}"]
            gen = gen_map.get(entity.entity_id, 0)
            if gen:
                parts.append(f" gen{gen}")
            if identity:
                parts.append(f" [{'; '.join(identity[:3])}]")
            # Add recent behaviors (first 2, truncated)
            if behaviors:
                behavior_text = "; ".join(b[:60] for b in behaviors[:2])
                parts.append(f" |最近行为: {behavior_text}")
            if ch_range:
                parts.append(f" |{ch_range} mc{entity.mention_count}")

            desc = "".join(parts)
            lines.append(desc)
        return lines

    def _infer_generation_map(self, graph: TemporalContextGraph) -> dict[str, int]:
        """BFS from founder entities to infer generation numbers."""
        children_of: dict[str, list[str]] = defaultdict(list)
        has_parent: set[str] = set()
        for rel in graph.relations.values():
            if rel.relation_type == "FAMILY_OF" and rel.status == "active":
                children_of[rel.source_entity_id].append(rel.target_entity_id)
                has_parent.add(rel.target_entity_id)

        generations: dict[str, int] = {}
        queue: list[tuple[str, int]] = [
            (eid, 1) for eid in graph.entities.keys() if eid not in has_parent
        ]
        while queue:
            eid, gen = queue.pop(0)
            if eid in generations:
                continue
            generations[eid] = gen
            for child in children_of.get(eid, []):
                queue.append((child, gen + 1))
        return generations

    def _extract_window_with_llm(
        self,
        *,
        window: ExtractionWindow,
        graph: TemporalContextGraph,
    ) -> llm_extraction.EpisodeGraphExtraction | None:
        if self.extractor_runtime is None:
            return None

        first_chunk = window.core_chunks[0]
        gen_map = self._infer_generation_map(graph) if self.use_generation_info else {}

        known_entities = [
            llm_extraction.KnownEntityCandidate(
                entity_id=entity.entity_id,
                canonical_name=entity.canonical_name,
                entity_type=entity.entity_type,
                aliases=entity.aliases,
                mention_count=entity.mention_count,
                last_seen_chapter=entity.last_seen_chapter,
                last_seen_paragraph=entity.last_seen_paragraph,
                summary=entity.summary,
                generation=gen_map.get(entity.entity_id, 0),
            )
            for entity in sorted(graph.entities.values(), key=lambda item: item.mention_count, reverse=True)
            if entity.last_seen_chapter < window.chapter_index
            or (
                entity.last_seen_chapter == window.chapter_index
                and entity.last_seen_paragraph < first_chunk.paragraph_index
            )
        ]

        family_tree = self._build_family_tree_context(graph)
        character_descriptions = self._build_character_description_cache(graph) if self.use_description_cache else None

        try:
            graph.metadata["llm_calls"] = int(graph.metadata.get("llm_calls", 0)) + 1
            self._emit_progress(
                stage="llm-request-dispatched",
                title="LLM window extraction",
                message=f"Dispatching extraction for {window.window_id} ({window.core_token_count} tokens, {len(window.core_chunks)} chunks).",
                current_snippet_id=window.window_id,
                current_chapter_index=window.chapter_index,
                current_paragraph_index=first_chunk.paragraph_index,
                details={
                    "phase": "llm-window-dispatched",
                    "window_id": window.window_id,
                    "core_token_count": window.core_token_count,
                    "prev_context_token_count": window.prev_context_token_count,
                    "chunk_count": len(window.core_chunks),
                    "provider": self.extractor_runtime.provider_label,
                },
            )
            extraction = llm_extraction.extract_window_graph_with_llm(
                runtime=self.extractor_runtime,
                core_text=window.core_text,
                prev_context_text=window.prev_context_text,
                chapter_index=window.chapter_index,
                known_entities=known_entities,
                family_tree_lines=family_tree,
                character_descriptions=character_descriptions,
            )
            self._emit_progress(
                stage="llm-response-received",
                title="LLM window response received",
                message=(
                    f"Received extraction for {window.window_id}: "
                    f"{len(extraction.entities)} entities, {len(extraction.facts)} facts."
                ),
                current_snippet_id=window.window_id,
                current_chapter_index=window.chapter_index,
                current_paragraph_index=first_chunk.paragraph_index,
                details={
                    "phase": "llm-window-received",
                    "window_id": window.window_id,
                    "entity_candidates": len(extraction.entities),
                    "fact_candidates": len(extraction.facts),
                },
            )
            return extraction
        except Exception as exc:
            graph.metadata.setdefault("llm_extraction_warnings", [])
            graph.metadata["llm_extraction_warnings"].append(
                {
                    "window_id": window.window_id,
                    "chapter_index": window.chapter_index,
                    "reason": str(exc),
                }
            )
            if self.strict_llm_extraction:
                raise RuntimeError(f"strict llm extraction failed for {window.window_id}: {exc}") from exc
            self._emit_progress(
                stage="llm-request-failed",
                title="LLM window extraction failed",
                message=f"LLM extraction failed for {window.window_id}; skipping window.",
                current_snippet_id=window.window_id,
                current_chapter_index=window.chapter_index,
                details={
                    "phase": "llm-window-failed",
                    "error": str(exc),
                },
            )
            return None

    def _consolidate_chapters(
        self,
        *,
        graph: TemporalContextGraph,
        chapter_entities: dict[int, set[str]],
        entity_id_by_alias: dict[str, str],
        active_relation_by_signature: dict[str, str],
        active_state_relation_by_key: dict[str, str],
    ) -> None:
        for chapter_index, entity_ids in chapter_entities.items():
            canonical_groups: dict[str, list[str]] = defaultdict(list)
            for entity_id in entity_ids:
                entity = graph.entities.get(entity_id)
                if entity is None:
                    continue
                canonical_groups[_slugify(entity.canonical_name)].append(entity_id)
            merged_pairs = 0
            for group in canonical_groups.values():
                if len(group) < 2:
                    continue
                primary_id = max(group, key=lambda item: graph.entities[item].mention_count)
                for secondary_id in group:
                    if secondary_id == primary_id or secondary_id not in graph.entities:
                        continue
                    self._merge_entity_into_primary(
                        graph=graph,
                        primary_id=primary_id,
                        secondary_id=secondary_id,
                        entity_id_by_alias=entity_id_by_alias,
                    )
                    entity_ids.discard(secondary_id)
                    entity_ids.add(primary_id)
                    merged_pairs += 1
            deduped_relations = self._deduplicate_relations_for_chapter(graph=graph, chapter_index=chapter_index)
            graph.metadata["chapter_consolidations"].append(
                {
                    "chapter_index": chapter_index,
                    "merged_entities": merged_pairs,
                    "deduplicated_relations": deduped_relations,
                }
            )
        self._rebuild_relation_indexes(
            graph=graph,
            active_relation_by_signature=active_relation_by_signature,
            active_state_relation_by_key=active_state_relation_by_key,
        )

    def _merge_entity_into_primary(
        self,
        *,
        graph: TemporalContextGraph,
        primary_id: str,
        secondary_id: str,
        entity_id_by_alias: dict[str, str],
    ) -> None:
        primary = graph.entities.get(primary_id)
        secondary = graph.entities.get(secondary_id)
        if primary is None or secondary is None:
            return
        primary.aliases = sorted(set(primary.aliases).union(secondary.aliases).union({secondary.canonical_name}))
        primary.mention_count += secondary.mention_count
        primary.episode_ids = sorted(set(primary.episode_ids).union(secondary.episode_ids))
        if primary.first_seen_chapter == 0 or (
            secondary.first_seen_chapter
            and (secondary.first_seen_chapter, secondary.first_seen_paragraph)
            < (primary.first_seen_chapter, primary.first_seen_paragraph)
        ):
            primary.first_seen_chapter = secondary.first_seen_chapter
            primary.first_seen_paragraph = secondary.first_seen_paragraph
        if (secondary.last_seen_chapter, secondary.last_seen_paragraph) > (
            primary.last_seen_chapter,
            primary.last_seen_paragraph,
        ):
            primary.last_seen_chapter = secondary.last_seen_chapter
            primary.last_seen_paragraph = secondary.last_seen_paragraph
        for alias in _entity_alias_forms(primary).union(_entity_alias_forms(secondary)):
            entity_id_by_alias[alias] = primary_id
        for episode in graph.episodes.values():
            if secondary_id in episode.entity_ids:
                episode.entity_ids = [primary_id if entity_id == secondary_id else entity_id for entity_id in episode.entity_ids]
                episode.entity_ids = sorted(set(episode.entity_ids))
        for chapter in graph.chapters.values():
            if secondary_id in chapter.entity_ids:
                chapter.entity_ids = sorted({primary_id if entity_id == secondary_id else entity_id for entity_id in chapter.entity_ids})
        for relation in graph.relations.values():
            if relation.source_entity_id == secondary_id:
                relation.source_entity_id = primary_id
            if relation.target_entity_id == secondary_id:
                relation.target_entity_id = primary_id
        del graph.entities[secondary_id]

    def _deduplicate_relations_for_chapter(self, *, graph: TemporalContextGraph, chapter_index: int) -> int:
        seen: dict[tuple[str, str, str], str] = {}
        removed = 0
        for edge_id, relation in list(graph.relations.items()):
            if relation.valid_at_chapter != chapter_index or relation.state_family not in CHAPTER_CONSOLIDATION_FAMILIES:
                continue
            relation.fact_signature = _fact_signature(
                relation_type=relation.relation_type,
                state_family=relation.state_family,
                source_entity_id=relation.source_entity_id,
                target_entity_id=relation.target_entity_id,
                directionality=relation.directionality,
            )
            dedupe_key = (relation.fact_signature, relation.status, str(relation.invalidated_by_edge_id or ""))
            existing_edge_id = seen.get(dedupe_key)
            if existing_edge_id is None:
                seen[dedupe_key] = edge_id
                continue
            existing = graph.relations[existing_edge_id]
            existing.weight += relation.weight
            existing.episode_ids = sorted(set(existing.episode_ids).union(relation.episode_ids))
            existing.provenance.extend(relation.provenance)
            existing.metadata["confidence"] = max(
                float(existing.metadata.get("confidence", 0.0)),
                float(relation.metadata.get("confidence", 0.0)),
            )
            for chapter in graph.chapters.values():
                if edge_id in chapter.relation_ids:
                    chapter.relation_ids = [existing_edge_id if rid == edge_id else rid for rid in chapter.relation_ids]
                    chapter.relation_ids = sorted(set(chapter.relation_ids))
            for episode in graph.episodes.values():
                if edge_id in episode.relation_ids:
                    episode.relation_ids = [existing_edge_id if rid == edge_id else rid for rid in episode.relation_ids]
                    episode.relation_ids = sorted(set(episode.relation_ids))
            del graph.relations[edge_id]
            removed += 1
        return removed

    def _rebuild_relation_indexes(
        self,
        *,
        graph: TemporalContextGraph,
        active_relation_by_signature: dict[str, str],
        active_state_relation_by_key: dict[str, str],
    ) -> None:
        active_relation_by_signature.clear()
        active_state_relation_by_key.clear()
        for relation in graph.relations.values():
            relation.fact_signature = _fact_signature(
                relation_type=relation.relation_type,
                state_family=relation.state_family,
                source_entity_id=relation.source_entity_id,
                target_entity_id=relation.target_entity_id,
                directionality=relation.directionality,
            )
            if relation.status == "active":
                active_relation_by_signature[relation.fact_signature] = relation.edge_id
                state_key = build_state_slot(
                    state_family=relation.state_family,
                    source_entity_id=relation.source_entity_id,
                    directionality=relation.directionality,
                    target_entity_id=relation.target_entity_id,
                    state_slot=relation.metadata.get("state_slot"),
                )
                if state_key:
                    active_state_relation_by_key[state_key] = relation.edge_id


    def _rebuild_state_from_graph(
        self,
        graph: TemporalContextGraph,
    ) -> tuple[dict[str, str], dict[str, str], dict[str, str], Counter[str]]:
        entity_id_by_alias: dict[str, str] = {}
        for entity in graph.entities.values():
            for alias in _entity_alias_forms(entity):
                entity_id_by_alias.setdefault(alias, entity.entity_id)
        active_relation_by_signature: dict[str, str] = {}
        active_state_relation_by_key: dict[str, str] = {}
        relation_version_counter: Counter[str] = Counter()
        for edge in graph.relations.values():
            edge.fact_signature = _fact_signature(
                relation_type=edge.relation_type,
                state_family=edge.state_family,
                source_entity_id=edge.source_entity_id,
                target_entity_id=edge.target_entity_id,
                directionality=edge.directionality,
            )
            base_slug = _slugify(edge.fact_signature)
            version = int(edge.edge_id.rsplit("_v", 1)[-1]) if "_v" in edge.edge_id else 1
            relation_version_counter[base_slug] = max(relation_version_counter.get(base_slug, 0), version)
            if edge.status == "active":
                active_relation_by_signature[edge.fact_signature] = edge.edge_id
                state_key = build_state_slot(
                    state_family=edge.state_family,
                    source_entity_id=edge.source_entity_id,
                    directionality=edge.directionality,
                    target_entity_id=edge.target_entity_id,
                    state_slot=edge.metadata.get("state_slot"),
                )
                if state_key:
                    active_state_relation_by_key[state_key] = edge.edge_id
        return entity_id_by_alias, active_relation_by_signature, active_state_relation_by_key, relation_version_counter

    def _rebuild_chapter_indexes(
        self,
        graph: TemporalContextGraph,
    ) -> tuple[dict[int, set[str]], dict[int, list[str]], dict[int, set[str]]]:
        chapter_entities: dict[int, set[str]] = defaultdict(set)
        chapter_episode_ids: dict[int, list[str]] = defaultdict(list)
        chapter_relation_ids: dict[int, set[str]] = defaultdict(set)
        for ep in graph.episodes.values():
            chapter_episode_ids[ep.chapter_index].append(ep.episode_id)
            chapter_entities[ep.chapter_index].update(ep.entity_ids)
            chapter_relation_ids[ep.chapter_index].update(ep.relation_ids)
        return chapter_entities, chapter_episode_ids, chapter_relation_ids


def build_temporal_graph(book: BookRecord) -> TemporalContextGraph:
    return TemporalGraphBuilder().build(book)
