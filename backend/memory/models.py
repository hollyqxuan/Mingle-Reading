from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


MEMORY_VERSION = "1.0.0"


class EvidenceSpan(BaseModel):
    chunk_id: str
    chapter_index: int
    paragraph_index: int
    text_excerpt: str = ""
    start: int | None = None
    end: int | None = None
    source: str = "book_text"


class EntityMention(BaseModel):
    chunk_id: str
    chapter_index: int
    paragraph_index: int
    surface: str
    text_excerpt: str = ""
    start: int | None = None
    end: int | None = None


class RegistryEntity(BaseModel):
    entity_id: str
    canonical_name: str
    aliases: list[str] = Field(default_factory=list)
    entity_type: str = "unknown"
    first_seen: dict[str, int] = Field(default_factory=dict)
    last_seen: dict[str, int] = Field(default_factory=dict)
    mentions: list[EntityMention] = Field(default_factory=list)
    evidence_spans: list[EvidenceSpan] = Field(default_factory=list)
    confidence: float = 0.0
    merge_warnings: list[str] = Field(default_factory=list)
    summary: str = ""


class MemoryNode(BaseModel):
    memory_id: str
    memory_type: Literal["episode", "chapter", "character_arc", "theme_arc"]
    title: str
    summary: str
    chapter_index: int = 0
    paragraph_index: int = 0
    entity_ids: list[str] = Field(default_factory=list)
    relation_ids: list[str] = Field(default_factory=list)
    evidence_chunk_ids: list[str] = Field(default_factory=list)
    salience: float = 0.0
    metadata: dict[str, Any] = Field(default_factory=dict)


class MemoryIndex(BaseModel):
    book_id: str
    title: str
    graph_version: str = MEMORY_VERSION
    memory_version: str = MEMORY_VERSION
    status: Literal["ready", "degraded", "missing", "building", "failed"] = "ready"
    degraded_reasons: list[str] = Field(default_factory=list)
    entities: dict[str, RegistryEntity] = Field(default_factory=dict)
    memories: dict[str, MemoryNode] = Field(default_factory=dict)
    retrieval_documents: list[dict[str, Any]] = Field(default_factory=list)
    embedding: dict[str, Any] = Field(default_factory=dict)
    audit: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def entity_list(self) -> list[RegistryEntity]:
        return sorted(
            self.entities.values(),
            key=lambda item: (len(item.mentions), item.last_seen.get("chapter", 0)),
            reverse=True,
        )
