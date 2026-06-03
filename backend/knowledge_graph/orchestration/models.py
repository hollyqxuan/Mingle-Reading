from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class ReadingProgress(BaseModel):
    book_id: str
    chapter_id: int
    section_id: int | None = None
    paragraph_id: int | None = None
    token_offset: int | None = None
    scroll_offset: float | None = None
    dwell_seconds: float | None = None
    updated_at: str | None = None


class SelectionAnchor(BaseModel):
    chapter_id: int
    section_id: int | None = None
    paragraph_id: int | None = None


class SelectionContext(BaseModel):
    book_id: str
    selection_id: str | None = None
    selected_text: str = ""
    left_context: str = ""
    right_context: str = ""
    anchor: SelectionAnchor | None = None

    @property
    def combined_text(self) -> str:
        parts = [self.left_context.strip(), self.selected_text.strip(), self.right_context.strip()]
        return " ".join(part for part in parts if part)


class RetrievalFilters(BaseModel):
    max_chapter_id: int
    max_paragraph_id: int | None = None
    max_token_offset: int | None = None
    character_ids: list[str] = Field(default_factory=list)
    theme_tags: list[str] = Field(default_factory=list)
    exclude_spoiler_levels: list[str] = Field(default_factory=lambda: ["future_explicit"])


class RetrievalRequest(BaseModel):
    request_id: str
    scope: Literal["book_text", "graph", "mixed"] = "mixed"
    window_mode: Literal["visible", "recent", "historical"] = "visible"
    kb_id: str
    query: str
    selection_context: SelectionContext | None = None
    reading_progress: ReadingProgress
    filters: RetrievalFilters
    top_k: int = 6


class RetrievalHit(BaseModel):
    chunk_id: str
    source_type: Literal["book_text", "graph"]
    score: float
    text: str
    chapter_id: int
    section_id: int | None = None
    paragraph_id: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class Citation(BaseModel):
    chunk_id: str
    source_type: Literal["book_text", "graph"]
    chapter_id: int
    section_id: int | None = None
    paragraph_id: int | None = None
    score: float


class GuardrailTrace(BaseModel):
    spoiler_guard: bool = True
    filter_first: bool = True
    progress_consistent: bool = True
    max_chapter_id: int
    max_paragraph_id: int | None = None
    max_token_offset: int | None = None
    visible_chunk_count: int = 0
    filtered_out_chunk_count: int = 0
    selection_anchor_chapter_id: int | None = None
    selected_character_filters: list[str] = Field(default_factory=list)
    selected_theme_filters: list[str] = Field(default_factory=list)
    retrieval_plan: list[str] = Field(default_factory=list)
    retrieval_counts: dict[str, int] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class EntityNetworkResult(BaseModel):
    """Complete ego-network for a single entity — all relations, neighbours, communities, sagas."""

    entity_id: str
    canonical_name: str
    entity_type: str
    mention_count: int
    first_seen_chapter: int
    last_seen_chapter: int
    summary: str = ""
    aliases: list[str] = Field(default_factory=list)
    relations: list[dict[str, Any]] = Field(default_factory=list)
    neighbour_entities: list[dict[str, Any]] = Field(default_factory=list)
    communities: list[dict[str, Any]] = Field(default_factory=list)
    sagas: list[dict[str, Any]] = Field(default_factory=list)


class OrchestrationResult(BaseModel):
    request_id: str
    retrieval_request: RetrievalRequest
    hits: list[RetrievalHit]
    citations: list[Citation]
    guardrail_trace: GuardrailTrace
    retrieval_trace: dict[str, Any] = Field(default_factory=dict)
    structured_context: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def citations_match_hits(self) -> "OrchestrationResult":
        hit_ids = {hit.chunk_id for hit in self.hits}
        self.citations = [citation for citation in self.citations if citation.chunk_id in hit_ids]
        return self
