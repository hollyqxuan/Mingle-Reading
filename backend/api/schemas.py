from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class BookChunk(BaseModel):
    chunk_id: str
    book_id: str
    chapter_id: str
    section_id: str | None = None
    paragraph_start_id: str | None = None
    paragraph_end_id: str | None = None
    chunk_level: Literal["l0_raw_paragraph", "l1_fine_grained"] = "l0_raw_paragraph"
    chapter_index: int
    paragraph_id: str
    paragraph_index: int
    text: str
    token_offset: int = 0
    spoiler_level: int = 0
    position: dict[str, int] = Field(default_factory=dict)
    spoiler_guard: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)
    candidate_characters: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class BookRecord(BaseModel):
    book_id: str
    title: str
    source_path: str
    chapter_count: int
    chunks: list[BookChunk]


class UploadResponse(BaseModel):
    book_id: str
    title: str
    chapter_count: int
    chunk_count: int


class QuestionRequest(BaseModel):
    book_id: str
    question: str
    highlight_text: str = ""
    current_chapter: int = 1
    persona_id: str = "neutral"
    top_k: int = 4
    conversation_history: list["ChatMessage"] = Field(default_factory=list)


class RetrievedContext(BaseModel):
    chunk_id: str
    chapter_index: int
    paragraph_index: int
    score: float
    text: str


class AnswerCitation(BaseModel):
    chunk_id: str
    chapter_index: int
    paragraph_index: int = 0
    source_type: Literal["book_text", "graph", "memory"] = "book_text"
    quote: str = ""
    score: float = 0.0


class AnswerClaim(BaseModel):
    text: str
    supported: bool = True
    citation_chunk_ids: list[str] = Field(default_factory=list)


class QuestionResponse(BaseModel):
    answer: str
    persona_id: str
    safe: bool
    reason: str
    contexts: list[RetrievedContext]
    model_name: str = ""
    citations: list[AnswerCitation] = Field(default_factory=list)
    claims: list[AnswerClaim] = Field(default_factory=list)
    confidence: float = 0.0
    unsupported_claim_count: int = 0
    retrieval_trace_id: str = ""


class SummaryRequest(BaseModel):
    book_id: str
    current_chapter: int
    persona_id: str = "neutral"


class SummaryResponse(BaseModel):
    summary: str
    chapter_id: str
    persona_id: str
    model_name: str = ""


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class CharacterCandidate(BaseModel):
    character_id: str
    character_name: str
    mention_count: int
    chapter_hits: list[int] = Field(default_factory=list)
    preview: str = ""


class CharacterRelationship(BaseModel):
    target: str
    description: str


class CharacterProfileRequest(BaseModel):
    book_id: str
    character_name: str
    current_chapter: int = 1


class CharacterProfile(BaseModel):
    character_id: str
    character_name: str
    summary: str
    core_traits: list[str] = Field(default_factory=list)
    relationships: list[CharacterRelationship] = Field(default_factory=list)
    signature_tension: str = ""
    evidence_chunk_ids: list[str] = Field(default_factory=list)
    current_scope: str = ""
    model_name: str = ""
    entity_id: str = ""
    arc_summary: str = ""
    visible_relationship_count: int = 0
    evidence_count: int = 0


class CharacterChatRequest(BaseModel):
    book_id: str
    character_name: str
    question: str
    current_chapter: int = 1
    conversation_history: list[ChatMessage] = Field(default_factory=list)
    top_k: int = 6


class CharacterChatResponse(BaseModel):
    answer: str
    character_name: str
    safe: bool
    reason: str
    model_name: str = ""
    profile: CharacterProfile


class InlineBubbleRequest(BaseModel):
    book_id: str
    current_chapter: int
    visible_chunk_ids: list[str] = Field(default_factory=list)
    persona_id: str = "persona_lu_xun"
    assistant_mode: Literal["persona", "character"] = "persona"
    character_name: str = ""
    max_bubbles: int = 3


class InlineBubble(BaseModel):
    bubble_id: str
    chunk_id: str
    anchor_text: str
    label: str
    comment: str
    emphasis: Literal["theme", "emotion", "relation", "foreshadow", "detail"] = "detail"
    bubble_type: Literal["detail", "emotion", "relation", "theme", "question", "character_inner_voice"] = "detail"
    source_entity_ids: list[str] = Field(default_factory=list)
    citation_chunk_ids: list[str] = Field(default_factory=list)
    trigger_reason: str = ""


class PersonaProfile(BaseModel):
    persona_id: str
    name: str
    source_type: Literal["literary_master", "book_character", "neutral"]
    style_traits: list[str]
    reasoning_style: list[str]
    citation: str
    prompt_scaffold: list[str] = Field(default_factory=list)


class PersonaPromptTraits(BaseModel):
    system_role: str = ""
    opening_instruction: str = ""
    tone_keywords: list[str] = Field(default_factory=list)
    reasoning_steps: list[str] = Field(default_factory=list)
    forbidden_patterns: list[str] = Field(default_factory=list)
    response_policies: list[str] = Field(default_factory=list)


class PersonaAgentConfig(BaseModel):
    agent_id: str
    persona_id: str
    display_name: str
    language: str = "zh-CN"
    api_key_env_var: str
    base_url_env_var: str
    model_name_env_var: str
    default_base_url: str = ""
    default_model_name: str = ""
    persona_pack_path: str = ""
    catalog_path: str = ""
    prompt_traits: PersonaPromptTraits = Field(default_factory=PersonaPromptTraits)
    enabled: bool = True


class PersonaCatalogSummary(BaseModel):
    total_sources: int = 0
    works: int = 0
    voice_sources: int = 0
    biography_and_critical: int = 0


class PersonaAgentStatus(BaseModel):
    agent_id: str
    persona_id: str
    display_name: str
    language: str
    api_key_env_var: str
    base_url_env_var: str
    model_name_env_var: str
    resolved_base_url: str
    resolved_model_name: str
    has_api_key: bool = False
    persona_pack_path: str = ""
    catalog_path: str = ""
    catalog_summary: PersonaCatalogSummary = Field(default_factory=PersonaCatalogSummary)
    prompt_traits: PersonaPromptTraits = Field(default_factory=PersonaPromptTraits)


class PersonaKnowledgeBundle(BaseModel):
    config: PersonaAgentConfig
    profile: PersonaProfile
    catalog_summary: PersonaCatalogSummary = Field(default_factory=PersonaCatalogSummary)
    persona_pack: dict[str, Any] = Field(default_factory=dict)
    catalog: dict[str, Any] = Field(default_factory=dict)


class PersonaRAGQueryRequest(BaseModel):
    query: str
    top_k: int = 5
    categories: list[str] = Field(default_factory=list)


class PersonaRAGHit(BaseModel):
    snippet_id: str
    title: str
    source_category: str
    snippet_type: str
    text: str
    score: float
    retrieval_weight: float


class PersonaPromptPreviewRequest(BaseModel):
    book_context: str
    question: str = ""
    top_k: int = 5
    categories: list[str] = Field(default_factory=list)


class PersonaPromptPreview(BaseModel):
    persona_id: str
    display_name: str
    model_name: str
    base_url: str
    has_api_key: bool
    system_prompt: str
    persona_context: str
    retrieved_hits: list[PersonaRAGHit] = Field(default_factory=list)


class ReadingProgress(BaseModel):
    book_id: str
    chapter_id: int
    section_id: int = 0
    paragraph_id: int = 0
    token_offset: int = 0
    scroll_offset: float = 0.0
    dwell_seconds: int = 0
    updated_at: str = ""


class SelectionAnchor(BaseModel):
    chapter_id: int
    section_id: int = 0
    paragraph_id: int = 0


class SelectionContext(BaseModel):
    book_id: str
    selection_id: str = ""
    selected_text: str
    left_context: str = ""
    right_context: str = ""
    anchor: SelectionAnchor


class RetrievalRequest(BaseModel):
    request_id: str = ""
    scope: Literal["book_text", "graph", "mixed"] = "mixed"
    kb_id: str
    query: str
    selection_context: SelectionContext
    reading_progress: ReadingProgress
    filters: dict[str, Any] = Field(default_factory=dict)
    top_k: int = 6


class Citation(BaseModel):
    chunk_id: str
    chapter_id: int
    section_id: int = 0


class OrchestrationResult(BaseModel):
    answer: str
    citations: list[Citation] = Field(default_factory=list)
    contexts: list[RetrievedContext] = Field(default_factory=list)
    guardrail_trace: dict[str, Any] = Field(default_factory=dict)
    retrieval_trace: dict[str, Any] = Field(default_factory=dict)
