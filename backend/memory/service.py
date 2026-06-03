from __future__ import annotations

import json
import math
import os
import re
import shutil
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any
from uuid import uuid4

from backend.api.schemas import BookChunk, BookRecord
from backend.config import ARCHIVE_DIR, GRAPHS_DIR, INDEXES_DIR
from backend.knowledge_graph.models import TemporalContextGraph
from backend.knowledge_graph.storage import graph_exists, graph_path, load_graph, save_graph
from backend.memory.models import (
    MEMORY_VERSION,
    EntityMention,
    EvidenceSpan,
    MemoryIndex,
    MemoryNode,
    RegistryEntity,
)


BOOK_STOPWORDS = {
    "Chapter",
    "Reader",
    "Library",
    "Mingle",
    "Reading",
    "AI",
}


@dataclass
class MemoryJobState:
    job_id: str
    book_id: str
    status: str = "queued"
    stage: str = "queued"
    title: str = "Memory rebuild queued"
    message: str = "Waiting for the memory rebuild pipeline to start."
    percent: int = 0
    error: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "book_id": self.book_id,
            "status": self.status,
            "stage": self.stage,
            "title": self.title,
            "message": self.message,
            "percent": self.percent,
            "error": self.error,
            "details": self.details,
        }


class MemoryJobRegistry:
    def __init__(self) -> None:
        self._lock = Lock()
        self._jobs: dict[str, MemoryJobState] = {}

    def create(self, book_id: str) -> MemoryJobState:
        job = MemoryJobState(job_id=f"memory-{uuid4().hex}", book_id=book_id)
        with self._lock:
            self._jobs[job.job_id] = job
        return job

    def get(self, job_id: str) -> MemoryJobState | None:
        with self._lock:
            return self._jobs.get(job_id)

    def update(self, job_id: str, **fields: Any) -> MemoryJobState:
        with self._lock:
            job = self._jobs[job_id]
            for key, value in fields.items():
                setattr(job, key, value)
            return job


memory_job_registry = MemoryJobRegistry()


def memory_index_path(book_id: str) -> Path:
    return INDEXES_DIR / f"{book_id}.memory.json"


def retrieval_index_path(book_id: str) -> Path:
    return INDEXES_DIR / f"{book_id}.retrieval.json"


def memory_index_exists(book_id: str) -> bool:
    return memory_index_path(book_id).exists()


def save_memory_index(index: MemoryIndex) -> None:
    INDEXES_DIR.mkdir(parents=True, exist_ok=True)
    memory_index_path(index.book_id).write_text(
        json.dumps(index.model_dump(mode="json"), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    retrieval_index_path(index.book_id).write_text(
        json.dumps(
            {
                "book_id": index.book_id,
                "memory_version": index.memory_version,
                "embedding": index.embedding,
                "documents": index.retrieval_documents,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def load_memory_index(book_id: str) -> MemoryIndex:
    payload = json.loads(memory_index_path(book_id).read_text(encoding="utf-8"))
    return MemoryIndex.model_validate(payload)


def archive_legacy_graph(book_id: str) -> str:
    path = graph_path(book_id)
    if not path.exists():
        return ""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("graph_version") == MEMORY_VERSION and payload.get("metadata", {}).get("memory_version") == MEMORY_VERSION:
            return ""
    except json.JSONDecodeError:
        pass
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    target_dir = ARCHIVE_DIR / "graphs_legacy" / stamp
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / path.name
    shutil.move(str(path), str(target))
    windows = GRAPHS_DIR / f"{book_id}.windows.jsonl"
    if windows.exists():
        shutil.move(str(windows), str(target_dir / windows.name))
    return str(target)


def embedding_runtime_status() -> dict[str, Any]:
    api_key, api_key_env_var = _first_env(
        "MINGLE_EMBEDDING_API_KEY",
        "MUSE_NEUTRAL_API_KEY",
        "OPENAI_API_KEY",
    )
    base_url, base_url_env_var = _first_env(
        "MINGLE_EMBEDDING_BASE_URL",
        "MUSE_NEUTRAL_BASE_URL",
        "OPENAI_BASE_URL",
    )
    model_name, model_name_env_var = _first_env(
        "MINGLE_EMBEDDING_MODEL_NAME",
        "MUSE_NEUTRAL_EMBEDDING_MODEL_NAME",
        "OPENAI_EMBEDDING_MODEL_NAME",
        "EMBEDDING_MODEL_NAME",
        "MUSE_NEUTRAL_MODEL_NAME",
    )
    configured = bool(api_key and base_url and model_name)
    missing = []
    if not api_key:
        missing.append("MINGLE_EMBEDDING_API_KEY or MUSE_NEUTRAL_API_KEY")
    if not base_url:
        missing.append("MINGLE_EMBEDDING_BASE_URL or MUSE_NEUTRAL_BASE_URL")
    if not model_name:
        missing.append("MINGLE_EMBEDDING_MODEL_NAME or MUSE_NEUTRAL_MODEL_NAME")
    return {
        "configured": configured,
        "base_url": base_url,
        "model_name": model_name,
        "api_key_env_var": api_key_env_var,
        "base_url_env_var": base_url_env_var,
        "model_name_env_var": model_name_env_var,
        "status": "ready" if configured else "degraded",
        "dimension": 0,
        "document_count": 0,
        "reason": "" if configured else f"embedding API env vars are not fully configured: {', '.join(missing)}",
    }


def build_memory_index(book: BookRecord, graph: TemporalContextGraph | None = None) -> MemoryIndex:
    degraded_reasons: list[str] = []
    if graph is None and graph_exists(book.book_id):
        try:
            graph = load_graph(book.book_id)
        except Exception as exc:  # pragma: no cover - defensive status path
            degraded_reasons.append(f"graph_load_failed: {exc}")

    if graph is None:
        degraded_reasons.append("graph_missing")
        graph = TemporalContextGraph(book_id=book.book_id, title=book.title)

    entities = _registry_from_graph_or_book(book, graph)
    memories = _build_memories(book, graph, entities)
    retrieval_documents = _build_retrieval_documents(book, graph, entities, memories)
    embedding = embedding_runtime_status()
    if embedding["configured"] and _should_skip_embedding_attempt(embedding):
        embedding["status"] = "degraded"
        embedding["reason"] = "configured LLM provider/model does not expose an embeddings endpoint; using keyword and graph retrieval"
        degraded_reasons.append(embedding["reason"])
    elif embedding["configured"]:
        try:
            _attach_embeddings(retrieval_documents, embedding)
        except Exception as exc:  # pragma: no cover - depends on external API
            embedding["status"] = "degraded"
            embedding["reason"] = f"embedding API failed: {exc}"
            degraded_reasons.append(embedding["reason"])
    else:
        degraded_reasons.append(embedding["reason"])
    if not graph.entities:
        degraded_reasons.append("graph has no resolved entities; rebuild with graph extractor credentials for full memory quality")

    status = "degraded" if degraded_reasons else "ready"
    return MemoryIndex(
        book_id=book.book_id,
        title=book.title,
        status=status,
        degraded_reasons=list(dict.fromkeys(reason for reason in degraded_reasons if reason)),
        entities=entities,
        memories=memories,
        retrieval_documents=retrieval_documents,
        embedding=embedding,
        audit=_build_audit(entities, graph),
        metadata={
            "created_at": datetime.now(UTC).isoformat(),
            "source": "graph+book",
            "graph_id": graph.graph_id,
            "graph_stats": graph.stats().model_dump(),
        },
    )


def rebuild_memory_from_book(book: BookRecord, graph_builder: Any | None = None, job_id: str | None = None) -> MemoryIndex:
    archived_path = archive_legacy_graph(book.book_id)
    if job_id:
        memory_job_registry.update(
            job_id,
            status="running",
            stage="graph-rebuild",
            title="Rebuilding temporal graph",
            message="Rebuilding graph and memory from the trusted book JSON.",
            percent=20,
            details={"archived_legacy_graph": archived_path},
        )
    graph = None
    if graph_builder is not None:
        graph = graph_builder.build(book)
        graph.graph_version = MEMORY_VERSION
        graph.metadata["memory_version"] = MEMORY_VERSION
        graph.metadata["legacy_graph_archived_at"] = archived_path
        save_graph(graph)
    if job_id:
        memory_job_registry.update(
            job_id,
            stage="memory-index",
            title="Building memory index",
            message="Building entity registry, layered memory, and retrieval documents.",
            percent=72,
        )
    index = build_memory_index(book, graph)
    save_memory_index(index)
    if job_id:
        memory_job_registry.update(
            job_id,
            status="completed",
            stage="completed",
            title="Memory ready",
            message=f"Memory rebuilt for {book.title}.",
            percent=100,
            details={
                "entity_count": len(index.entities),
                "memory_count": len(index.memories),
                "status": index.status,
                "degraded_reasons": index.degraded_reasons,
            },
        )
    return index


def memory_status(book: BookRecord) -> dict[str, Any]:
    graph_ready = graph_exists(book.book_id)
    index_ready = memory_index_exists(book.book_id)
    if index_ready:
        index = load_memory_index(book.book_id)
        return {
            "book_id": book.book_id,
            "title": book.title,
            "status": index.status,
            "graph_ready": graph_ready,
            "index_ready": True,
            "graph_version": MEMORY_VERSION if graph_ready else "",
            "memory_version": index.memory_version,
            "degraded_reasons": index.degraded_reasons,
            "entity_count": len(index.entities),
            "memory_count": len(index.memories),
            "embedding": index.embedding,
            "audit": index.audit,
        }
    reasons = []
    if not graph_ready:
        reasons.append("graph_missing")
    reasons.append("memory_index_missing")
    embedding = embedding_runtime_status()
    if not embedding["configured"]:
        reasons.append(embedding["reason"])
    return {
        "book_id": book.book_id,
        "title": book.title,
        "status": "missing" if not graph_ready else "degraded",
        "graph_ready": graph_ready,
        "index_ready": False,
        "graph_version": "",
        "memory_version": "",
        "degraded_reasons": list(dict.fromkeys(reasons)),
        "entity_count": 0,
        "memory_count": 0,
        "embedding": embedding,
        "audit": {},
    }


def ensure_memory_index(book: BookRecord) -> MemoryIndex:
    if memory_index_exists(book.book_id):
        return load_memory_index(book.book_id)
    index = build_memory_index(book)
    save_memory_index(index)
    return index


def list_entities(book: BookRecord, *, entity_type: str = "", chapter: int = 0, query: str = "") -> list[dict[str, Any]]:
    index = ensure_memory_index(book)
    terms = _tokenize(query)
    rows = []
    for entity in index.entity_list():
        if entity_type and entity.entity_type != entity_type:
            continue
        if chapter and entity.first_seen.get("chapter", 0) > chapter:
            continue
        searchable = " ".join([entity.canonical_name, *entity.aliases, entity.summary])
        if terms and not any(term in searchable.lower() for term in terms):
            continue
        rows.append(
            {
                "entity_id": entity.entity_id,
                "canonical_name": entity.canonical_name,
                "aliases": entity.aliases,
                "entity_type": entity.entity_type,
                "mention_count": len(entity.mentions),
                "first_seen": entity.first_seen,
                "last_seen": entity.last_seen,
                "summary": entity.summary,
                "confidence": entity.confidence,
                "merge_warnings": entity.merge_warnings,
            }
        )
    return rows


def entity_detail(book: BookRecord, entity_id: str) -> dict[str, Any]:
    index = ensure_memory_index(book)
    entity = index.entities.get(entity_id)
    if entity is None:
        raise KeyError(entity_id)
    graph = load_graph(book.book_id) if graph_exists(book.book_id) else None
    relations = []
    if graph is not None:
        for relation in graph.relations.values():
            if relation.source_entity_id != entity_id and relation.target_entity_id != entity_id:
                continue
            source = graph.entities.get(relation.source_entity_id)
            target = graph.entities.get(relation.target_entity_id)
            relations.append(
                {
                    "relation_id": relation.edge_id,
                    "source": source.canonical_name if source else relation.source_entity_id,
                    "target": target.canonical_name if target else relation.target_entity_id,
                    "relation_type": relation.relation_type,
                    "fact": relation.fact,
                    "status": relation.status,
                    "valid_at_chapter": relation.valid_at_chapter,
                    "valid_at_paragraph": relation.valid_at_paragraph,
                    "evidence_chunk_ids": [item.chunk_id for item in relation.provenance],
                }
            )
    memories = [
        memory.model_dump()
        for memory in index.memories.values()
        if entity_id in memory.entity_ids and memory.memory_type == "character_arc"
    ]
    return {
        **entity.model_dump(),
        "mention_count": len(entity.mentions),
        "relations": relations,
        "character_arc": memories,
    }


def memory_map(
    book: BookRecord,
    *,
    scope: str,
    chapter: int,
    paragraph: int = 0,
    entity_id: str = "",
    limit: int = 18,
) -> dict[str, Any]:
    index = ensure_memory_index(book)
    graph = load_graph(book.book_id) if graph_exists(book.book_id) else None
    if graph is None:
        return _memory_map_from_index(index, scope=scope, chapter=chapter, paragraph=paragraph, entity_id=entity_id, limit=limit)
    if scope == "character" and entity_id:
        seed_entity_ids = {entity_id}
    elif scope == "passage":
        seed_entity_ids = {
            entity_id
            for entity_id, entity in graph.entities.items()
            if entity.first_seen_chapter <= chapter
            and (not paragraph or entity.first_seen_chapter < chapter or entity.first_seen_paragraph <= paragraph)
        }
    else:
        seed_entity_ids = {
            entity_id
            for entity_id, entity in graph.entities.items()
            if entity.first_seen_chapter <= chapter and (scope != "chapter" or entity.last_seen_chapter >= chapter)
        }
    selected = sorted(
        [graph.entities[eid] for eid in seed_entity_ids if eid in graph.entities],
        key=lambda item: item.mention_count,
        reverse=True,
    )[:limit]
    selected_ids = {item.entity_id for item in selected}
    if scope == "character" and entity_id:
        for relation in graph.relations.values():
            if relation.valid_at_chapter > chapter:
                continue
            if relation.source_entity_id == entity_id:
                selected_ids.add(relation.target_entity_id)
            if relation.target_entity_id == entity_id:
                selected_ids.add(relation.source_entity_id)
        selected_ids = {entity_id, *list(selected_ids)[: max(limit - 1, 0)]}
    edges = []
    for relation in graph.relations.values():
        if relation.valid_at_chapter > chapter:
            continue
        if paragraph and relation.valid_at_chapter == chapter and relation.valid_at_paragraph > paragraph:
            continue
        if relation.source_entity_id not in selected_ids or relation.target_entity_id not in selected_ids:
            continue
        source_entity = graph.entities.get(relation.source_entity_id)
        target_entity = graph.entities.get(relation.target_entity_id)
        edges.append(
            {
                "id": relation.edge_id,
                "source": relation.source_entity_id,
                "target": relation.target_entity_id,
                "source_label": source_entity.canonical_name if source_entity else relation.source_entity_id,
                "target_label": target_entity.canonical_name if target_entity else relation.target_entity_id,
                "label": relation.relation_type,
                "relation_category": _relation_category(relation.relation_type, relation.fact),
                "state_family": getattr(relation, "state_family", ""),
                "fact": relation.fact,
                "status": relation.status,
                "weight": relation.weight,
                "valid_at_chapter": relation.valid_at_chapter,
                "valid_at_paragraph": relation.valid_at_paragraph,
                "citation_chunk_ids": [item.chunk_id for item in relation.provenance],
            }
        )
    nodes = []
    for entity_id_value in selected_ids:
        entity = index.entities.get(entity_id_value)
        graph_entity = graph.entities.get(entity_id_value)
        if entity is None and graph_entity is None:
            continue
        nodes.append(
            {
                "id": entity_id_value,
                "label": entity.canonical_name if entity else graph_entity.canonical_name,
                "type": entity.entity_type if entity else graph_entity.entity_type,
                "summary": entity.summary if entity else graph_entity.summary,
                "mention_count": len(entity.mentions) if entity else graph_entity.mention_count,
                "first_seen": entity.first_seen if entity else {"chapter": graph_entity.first_seen_chapter, "paragraph": graph_entity.first_seen_paragraph},
                "last_seen": entity.last_seen if entity else {"chapter": graph_entity.last_seen_chapter, "paragraph": graph_entity.last_seen_paragraph},
            }
        )
    return {
        "book_id": book.book_id,
        "title": book.title,
        "scope": scope,
        "chapter_index": chapter,
        "paragraph_index": paragraph,
        "stats": {"node_count": len(nodes), "edge_count": len(edges)},
        "nodes": sorted(nodes, key=lambda item: item["mention_count"], reverse=True)[:limit],
        "edges": sorted(edges, key=lambda item: item["weight"], reverse=True)[: max(limit + 6, 12)],
        "memories": _memory_cards(index, scope=scope, chapter=chapter, entity_id=entity_id),
    }


def _relation_category(relation_type: str = "", fact: str = "") -> str:
    text = f"{relation_type} {fact}".lower()
    if any(token in text for token in ["family", "parent", "child", "sibling", "spouse", "mother", "father", "亲", "父", "母", "子", "女", "兄", "弟", "姐", "妹", "夫", "妻"]):
        return "family"
    if any(token in text for token in ["conflict", "enemy", "oppose", "fight", "threat", "kill", "hate", "rival", "betray", "冲突", "敌", "杀", "恨", "威胁", "对抗", "背叛"]):
        return "conflict"
    if any(token in text for token in ["love", "friend", "ally", "trust", "help", "protect", "care", "mentor", "情", "友", "爱", "信任", "帮助", "保护", "师"]):
        return "affinity"
    if any(token in text for token in ["speak", "talk", "meet", "interact", "see", "ask", "answer", "spoke", "交谈", "相遇", "看见", "问", "答"]):
        return "interaction"
    if any(token in text for token in ["located", "location", "place", "live", "arrive", "leave", "位于", "地点", "居住", "来到", "离开"]):
        return "location"
    if any(token in text for token in ["theme", "symbol", "concept", "metaphor", "主题", "象征", "隐喻"]):
        return "theme"
    return "other"


def _memory_map_from_index(index: MemoryIndex, *, scope: str, chapter: int, paragraph: int, entity_id: str, limit: int) -> dict[str, Any]:
    entities = [
        entity
        for entity in index.entity_list()
        if entity.first_seen.get("chapter", 0) <= chapter and (not entity_id or entity.entity_id == entity_id)
    ][:limit]
    return {
        "book_id": index.book_id,
        "title": index.title,
        "scope": scope,
        "chapter_index": chapter,
        "paragraph_index": paragraph,
        "stats": {"node_count": len(entities), "edge_count": 0},
        "nodes": [
            {
                "id": entity.entity_id,
                "label": entity.canonical_name,
                "type": entity.entity_type,
                "summary": entity.summary,
                "mention_count": len(entity.mentions),
                "first_seen": entity.first_seen,
                "last_seen": entity.last_seen,
            }
            for entity in entities
        ],
        "edges": [],
        "memories": _memory_cards(index, scope=scope, chapter=chapter, entity_id=entity_id),
    }


def _registry_from_graph_or_book(book: BookRecord, graph: TemporalContextGraph) -> dict[str, RegistryEntity]:
    registry: dict[str, RegistryEntity] = {}
    for entity in graph.entities.values():
        mentions = []
        evidence = []
        for episode_id in entity.episode_ids:
            episode = graph.episodes.get(episode_id)
            if episode is None:
                continue
            excerpt = _clip(episode.text, 180)
            mentions.append(
                EntityMention(
                    chunk_id=episode.chunk_id,
                    chapter_index=episode.chapter_index,
                    paragraph_index=episode.paragraph_index,
                    surface=entity.canonical_name,
                    text_excerpt=excerpt,
                )
            )
            evidence.append(
                EvidenceSpan(
                    chunk_id=episode.chunk_id,
                    chapter_index=episode.chapter_index,
                    paragraph_index=episode.paragraph_index,
                    text_excerpt=excerpt,
                    source="graph_episode",
                )
            )
        first = {"chapter": entity.first_seen_chapter, "paragraph": entity.first_seen_paragraph}
        last = {"chapter": entity.last_seen_chapter, "paragraph": entity.last_seen_paragraph}
        registry[entity.entity_id] = RegistryEntity(
            entity_id=entity.entity_id,
            canonical_name=entity.canonical_name,
            aliases=sorted(set(entity.aliases + [entity.canonical_name])),
            entity_type=entity.entity_type,
            first_seen=first,
            last_seen=last,
            mentions=mentions[:80],
            evidence_spans=evidence[:20],
            confidence=float(entity.metadata.get("last_confidence", 0.7) or 0.7),
            merge_warnings=list(entity.metadata.get("merge_warnings", [])),
            summary=entity.summary,
        )
    if registry:
        return registry
    return _heuristic_registry_from_book(book)


def _heuristic_registry_from_book(book: BookRecord) -> dict[str, RegistryEntity]:
    registry: dict[str, RegistryEntity] = {}
    for chunk in book.chunks:
        names = list(chunk.candidate_characters)
        if not names:
            names = _extract_surface_names(chunk.text)
        for name in names:
            if not name or name in BOOK_STOPWORDS:
                continue
            entity_id = f"entity_{_slugify(name)}"
            start = chunk.text.find(name)
            mention = EntityMention(
                chunk_id=chunk.chunk_id,
                chapter_index=chunk.chapter_index,
                paragraph_index=chunk.paragraph_index,
                surface=name,
                text_excerpt=_clip(chunk.text, 180),
                start=start if start >= 0 else None,
                end=start + len(name) if start >= 0 else None,
            )
            if entity_id not in registry:
                registry[entity_id] = RegistryEntity(
                    entity_id=entity_id,
                    canonical_name=name,
                    aliases=[name],
                    entity_type="character",
                    first_seen={"chapter": chunk.chapter_index, "paragraph": chunk.paragraph_index},
                    last_seen={"chapter": chunk.chapter_index, "paragraph": chunk.paragraph_index},
                    confidence=0.35,
                    merge_warnings=["heuristic_entity_without_graph_extraction"],
                    summary=f"{name} appears in visible book text.",
                )
            entity = registry[entity_id]
            entity.mentions.append(mention)
            entity.evidence_spans.append(
                EvidenceSpan(
                    chunk_id=chunk.chunk_id,
                    chapter_index=chunk.chapter_index,
                    paragraph_index=chunk.paragraph_index,
                    text_excerpt=mention.text_excerpt,
                    start=mention.start,
                    end=mention.end,
                    source="heuristic_mention",
                )
            )
            entity.last_seen = {"chapter": chunk.chapter_index, "paragraph": chunk.paragraph_index}
    return registry


def _build_memories(book: BookRecord, graph: TemporalContextGraph, entities: dict[str, RegistryEntity]) -> dict[str, MemoryNode]:
    memories: dict[str, MemoryNode] = {}
    chunks_by_chapter: dict[int, list[BookChunk]] = defaultdict(list)
    for chunk in book.chunks:
        chunks_by_chapter[chunk.chapter_index].append(chunk)
        memories[f"episode::{chunk.chunk_id}"] = MemoryNode(
            memory_id=f"episode::{chunk.chunk_id}",
            memory_type="episode",
            title=f"Chapter {chunk.chapter_index} paragraph {chunk.paragraph_index}",
            summary=_clip(chunk.text, 220),
            chapter_index=chunk.chapter_index,
            paragraph_index=chunk.paragraph_index,
            entity_ids=_episode_entity_ids(graph, chunk.chunk_id),
            relation_ids=_episode_relation_ids(graph, chunk.chunk_id),
            evidence_chunk_ids=[chunk.chunk_id],
            salience=_salience(chunk.text),
        )
    for chapter_index, chunks in chunks_by_chapter.items():
        text = " ".join(chunk.text for chunk in chunks[:8])
        entity_ids = sorted(
            {
                entity_id
                for entity_id, entity in entities.items()
                if entity.first_seen.get("chapter", 0) <= chapter_index <= entity.last_seen.get("chapter", 0)
            }
        )
        memories[f"chapter::{chapter_index:03d}"] = MemoryNode(
            memory_id=f"chapter::{chapter_index:03d}",
            memory_type="chapter",
            title=f"Chapter {chapter_index}",
            summary=_clip(text, 360),
            chapter_index=chapter_index,
            entity_ids=entity_ids[:18],
            evidence_chunk_ids=[chunk.chunk_id for chunk in chunks[:8]],
            salience=max((_salience(chunk.text) for chunk in chunks), default=0.0),
        )
    for entity_id, entity in entities.items():
        if entity.entity_type != "character":
            continue
        memories[f"character::{entity_id}"] = MemoryNode(
            memory_id=f"character::{entity_id}",
            memory_type="character_arc",
            title=entity.canonical_name,
            summary=entity.summary or f"{entity.canonical_name} appears from chapter {entity.first_seen.get('chapter', '-')}.",
            chapter_index=entity.last_seen.get("chapter", 0),
            paragraph_index=entity.last_seen.get("paragraph", 0),
            entity_ids=[entity_id],
            evidence_chunk_ids=[mention.chunk_id for mention in entity.mentions[:10]],
            salience=min(1.0, 0.25 + len(entity.mentions) / 20),
        )
    theme_words = _top_theme_terms(book.chunks)
    for index, (term, count) in enumerate(theme_words[:8], start=1):
        memories[f"theme::{_slugify(term)}"] = MemoryNode(
            memory_id=f"theme::{_slugify(term)}",
            memory_type="theme_arc",
            title=term,
            summary=f"主题词“{term}”在当前文本中出现 {count} 次，可作为阅读线索。",
            chapter_index=1,
            evidence_chunk_ids=[
                chunk.chunk_id for chunk in book.chunks if term in chunk.text
            ][:8],
            salience=min(1.0, count / 20),
        )
    return memories


def _build_retrieval_documents(
    book: BookRecord,
    graph: TemporalContextGraph,
    entities: dict[str, RegistryEntity],
    memories: dict[str, MemoryNode],
) -> list[dict[str, Any]]:
    docs: list[dict[str, Any]] = []
    for chunk in book.chunks:
        docs.append(
            {
                "document_id": f"chunk::{chunk.chunk_id}",
                "source_type": "book_text",
                "chunk_id": chunk.chunk_id,
                "chapter_index": chunk.chapter_index,
                "paragraph_index": chunk.paragraph_index,
                "text": chunk.text,
                "embedding": None,
            }
        )
    for entity in entities.values():
        docs.append(
            {
                "document_id": f"entity::{entity.entity_id}",
                "source_type": "entity",
                "entity_id": entity.entity_id,
                "chapter_index": entity.first_seen.get("chapter", 0),
                "paragraph_index": entity.first_seen.get("paragraph", 0),
                "text": " ".join([entity.canonical_name, *entity.aliases, entity.summary]),
                "embedding": None,
            }
        )
    for relation in graph.relations.values():
        docs.append(
            {
                "document_id": f"relation::{relation.edge_id}",
                "source_type": "relation",
                "relation_id": relation.edge_id,
                "chapter_index": relation.valid_at_chapter,
                "paragraph_index": relation.valid_at_paragraph,
                "text": relation.fact,
                "embedding": None,
            }
        )
    for memory in memories.values():
        docs.append(
            {
                "document_id": memory.memory_id,
                "source_type": f"memory_{memory.memory_type}",
                "chapter_index": memory.chapter_index,
                "paragraph_index": memory.paragraph_index,
                "text": f"{memory.title}\n{memory.summary}",
                "embedding": None,
            }
        )
    return docs


def _attach_embeddings(documents: list[dict[str, Any]], embedding: dict[str, Any]) -> None:
    texts = [str(document.get("text", ""))[:8000] for document in documents]
    vectors: list[list[float]] = []
    batch_size = _embedding_batch_size()
    for start in range(0, len(texts), batch_size):
        vectors.extend(_request_embeddings(texts[start : start + batch_size], embedding))
    if len(vectors) != len(documents):
        raise RuntimeError(f"embedding count mismatch: expected {len(documents)}, got {len(vectors)}")
    dimension = len(vectors[0]) if vectors else 0
    for document, vector in zip(documents, vectors, strict=True):
        document["embedding"] = vector
    embedding["status"] = "ready"
    embedding["dimension"] = dimension
    embedding["document_count"] = len(documents)
    embedding["reason"] = ""


def _request_embeddings(texts: list[str], embedding: dict[str, Any]) -> list[list[float]]:
    api_key = os.getenv(str(embedding.get("api_key_env_var") or "MINGLE_EMBEDDING_API_KEY"), "").strip()
    endpoint = _embedding_endpoint(str(embedding.get("base_url", "")))
    payload = json.dumps(
        {
            "model": embedding["model_name"],
            "input": texts,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        endpoint,
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")[:300]
        raise RuntimeError(f"{exc.code} {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(str(exc.reason)) from exc
    data = json.loads(body)
    rows = sorted(data.get("data", []), key=lambda item: item.get("index", 0))
    vectors = [row.get("embedding") for row in rows]
    if not all(isinstance(vector, list) for vector in vectors):
        raise RuntimeError("embedding response missing vector data")
    return vectors


def _embedding_batch_size() -> int:
    raw_value = os.getenv("MINGLE_EMBEDDING_BATCH_SIZE", "").strip()
    if raw_value.isdigit():
        return max(1, min(int(raw_value), 10))
    return 10


def _embedding_endpoint(base_url: str) -> str:
    clean = base_url.rstrip("/")
    if clean.endswith("/chat/completions"):
        return f"{clean.removesuffix('/chat/completions')}/embeddings"
    if clean.endswith("/embeddings"):
        return clean
    if clean.endswith("/v1"):
        return f"{clean}/embeddings"
    return f"{clean}/v1/embeddings"


def _first_env(*keys: str) -> tuple[str, str]:
    for key in keys:
        value = os.getenv(key, "").strip()
        if value:
            return value, key
    return "", keys[0] if keys else ""


def _should_skip_embedding_attempt(embedding: dict[str, Any]) -> bool:
    base_url = str(embedding.get("base_url", "")).lower()
    model_name = str(embedding.get("model_name", "")).lower()
    model_env = str(embedding.get("model_name_env_var", ""))
    if model_env != "MUSE_NEUTRAL_MODEL_NAME":
        return False
    if "deepseek" in base_url:
        return True
    chat_model_markers = ("chat", "reasoner", "pro", "flash", "turbo", "gpt-4", "claude", "gemini")
    return any(marker in model_name for marker in chat_model_markers)


def _build_audit(entities: dict[str, RegistryEntity], graph: TemporalContextGraph) -> dict[str, Any]:
    duplicate_names = [
        name
        for name, count in Counter(entity.canonical_name for entity in entities.values()).items()
        if count > 1
    ]
    low_confidence = [
        entity.entity_id
        for entity in entities.values()
        if entity.confidence < 0.45
    ][:30]
    relation_endpoint_warnings = [
        relation.edge_id
        for relation in graph.relations.values()
        if relation.source_entity_id not in entities or relation.target_entity_id not in entities
    ][:30]
    return {
        "duplicate_canonical_names": duplicate_names,
        "low_confidence_entity_ids": low_confidence,
        "relation_endpoint_warnings": relation_endpoint_warnings,
        "entity_count": len(entities),
        "relation_count": len(graph.relations),
    }


def _memory_cards(index: MemoryIndex, *, scope: str, chapter: int, entity_id: str) -> list[dict[str, Any]]:
    cards = []
    for memory in index.memories.values():
        if memory.chapter_index and memory.chapter_index > chapter and scope != "character":
            continue
        if entity_id and entity_id not in memory.entity_ids:
            continue
        if scope == "chapter" and memory.memory_type not in {"chapter", "theme_arc", "character_arc"}:
            continue
        if scope == "passage" and memory.memory_type not in {"episode", "chapter"}:
            continue
        cards.append(memory.model_dump())
    cards.sort(key=lambda item: item.get("salience", 0), reverse=True)
    return cards[:8]


def _episode_entity_ids(graph: TemporalContextGraph, chunk_id: str) -> list[str]:
    for episode in graph.episodes.values():
        if episode.chunk_id == chunk_id:
            return list(episode.entity_ids)
    return []


def _episode_relation_ids(graph: TemporalContextGraph, chunk_id: str) -> list[str]:
    for episode in graph.episodes.values():
        if episode.chunk_id == chunk_id:
            return list(episode.relation_ids)
    return []


def _top_theme_terms(chunks: list[BookChunk]) -> list[tuple[str, int]]:
    counts: Counter[str] = Counter()
    for chunk in chunks:
        for term in re.findall(r"[\u4e00-\u9fff]{2,4}", chunk.text):
            if term in {"我们", "他们", "这个", "那个", "自己", "因为", "所以", "但是", "没有", "一个"}:
                continue
            counts[term] += 1
    return counts.most_common(12)


def _extract_surface_names(text: str) -> list[str]:
    names = []
    names.extend(re.findall(r"\b[A-Z][a-zA-Z]{2,}(?:\s+[A-Z][a-zA-Z]{2,})?\b", text))
    quoted = re.findall(r"[《“](.{2,8}?)[》”]", text)
    names.extend(item for item in quoted if not re.search(r"[，。！？、]", item))
    return list(dict.fromkeys(names))[:8]


def _salience(text: str) -> float:
    markers = ["死", "爱", "恨", "哭", "笑", "梦", "秘密", "冲突", "离开", "发现", "为什么", "孤独", "自由"]
    score = sum(1 for marker in markers if marker in text)
    length_bonus = min(len(text) / 800, 0.4)
    return round(min(1.0, 0.15 * score + length_bonus), 3)


def _tokenize(text: str) -> list[str]:
    return [token for token in re.split(r"[^a-zA-Z0-9\u4e00-\u9fff]+", text.lower()) if token]


def _slugify(value: str) -> str:
    lowered = value.strip().lower()
    lowered = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "_", lowered)
    return lowered.strip("_") or "unknown"


def _clip(text: str, limit: int) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1] + "…"


def score_text(query: str, text: str) -> float:
    terms = _tokenize(query)
    if not terms:
        return 0.0
    lowered = text.lower()
    overlap = sum(1 for term in terms if term and term in lowered)
    return overlap / math.sqrt(max(len(text), 1))
