from __future__ import annotations

from backend.knowledge_graph.models import (
    CommunityNode,
    EntityNode,
    EpisodeNode,
    GraphProvenance,
    GraphQuery,
    RelationEdge,
    SagaNode,
    TemporalContextGraph,
)
from backend.knowledge_graph.retrieval import TemporalGraphRetriever


def _build_graph() -> TemporalContextGraph:
    graph = TemporalContextGraph(book_id="demo-book", title="Demo Book")
    graph.episodes["episode_001_001"] = EpisodeNode(
        episode_id="episode_001_001",
        book_id="demo-book",
        chunk_id="chunk-1",
        chapter_id="chapter-001",
        chapter_index=1,
        paragraph_id="paragraph-001",
        paragraph_index=1,
        episode_index=1,
        text="何塞发现了一艘西班牙大帆船。",
        entity_ids=["entity_jose", "entity_ship"],
        relation_ids=["edge_discovered_ship"],
        community_ids=["community_001"],
        saga_ids=["saga_001"],
        provenance=[GraphProvenance(chunk_id="chunk-1", book_id="demo-book", chapter_id="chapter-001", chapter_index=1, paragraph_id="paragraph-001", paragraph_index=1, text_excerpt="何塞发现了一艘西班牙大帆船。")],
    )
    graph.episodes["episode_002_001"] = EpisodeNode(
        episode_id="episode_002_001",
        book_id="demo-book",
        chunk_id="chunk-2",
        chapter_id="chapter-002",
        chapter_index=2,
        paragraph_id="paragraph-001",
        paragraph_index=1,
        episode_index=2,
        text="梅尔基亚德斯死于热病。",
        entity_ids=["entity_melquiades", "entity_singapore"],
        relation_ids=["edge_died_at"],
        provenance=[GraphProvenance(chunk_id="chunk-2", book_id="demo-book", chapter_id="chapter-002", chapter_index=2, paragraph_id="paragraph-001", paragraph_index=1, text_excerpt="梅尔基亚德斯死于热病。")],
    )
    graph.entities["entity_jose"] = EntityNode(entity_id="entity_jose", canonical_name="何塞·阿尔卡蒂奥·布恩迪亚", entity_type="character", mention_count=2, first_seen_chapter=1, first_seen_paragraph=1, last_seen_chapter=1, last_seen_paragraph=1, summary="Exploring founder.", episode_ids=["episode_001_001"])
    graph.entities["entity_ship"] = EntityNode(entity_id="entity_ship", canonical_name="西班牙大帆船", entity_type="artifact", mention_count=1, first_seen_chapter=1, first_seen_paragraph=1, last_seen_chapter=1, last_seen_paragraph=1, summary="A discovered ship.", episode_ids=["episode_001_001"])
    graph.entities["entity_melquiades"] = EntityNode(entity_id="entity_melquiades", canonical_name="梅尔基亚德斯", entity_type="character", mention_count=1, first_seen_chapter=2, first_seen_paragraph=1, last_seen_chapter=2, last_seen_paragraph=1, summary="Gypsy sage.", episode_ids=["episode_002_001"])
    graph.entities["entity_singapore"] = EntityNode(entity_id="entity_singapore", canonical_name="新加坡", entity_type="location", mention_count=1, first_seen_chapter=2, first_seen_paragraph=1, last_seen_chapter=2, last_seen_paragraph=1, summary="Death location.", episode_ids=["episode_002_001"])
    graph.relations["edge_discovered_ship"] = RelationEdge(
        edge_id="edge_discovered_ship",
        source_entity_id="entity_jose",
        target_entity_id="entity_ship",
        relation_type="DISCOVERED",
        state_family="context",
        directionality="directed",
        fact="何塞·阿尔卡蒂奥·布恩迪亚的远征队发现了一艘西班牙大帆船",
        fact_signature="DISCOVERED|context|entity_jose|entity_ship",
        valid_at_chapter=1,
        valid_at_paragraph=1,
        created_at="2026-01-01T00:00:00Z",
        episode_ids=["episode_001_001"],
        provenance=[GraphProvenance(chunk_id="chunk-1", book_id="demo-book", chapter_id="chapter-001", chapter_index=1, paragraph_id="paragraph-001", paragraph_index=1, text_excerpt="何塞发现了一艘西班牙大帆船。")],
    )
    graph.relations["edge_died_at"] = RelationEdge(
        edge_id="edge_died_at",
        source_entity_id="entity_melquiades",
        target_entity_id="entity_singapore",
        relation_type="DIED_AT",
        state_family="context",
        directionality="directed",
        fact="梅尔基亚德斯在新加坡的沙洲上死于热病",
        fact_signature="DIED_AT|context|entity_melquiades|entity_singapore",
        valid_at_chapter=2,
        valid_at_paragraph=1,
        created_at="2026-01-02T00:00:00Z",
        episode_ids=["episode_002_001"],
        provenance=[GraphProvenance(chunk_id="chunk-2", book_id="demo-book", chapter_id="chapter-002", chapter_index=2, paragraph_id="paragraph-001", paragraph_index=1, text_excerpt="梅尔基亚德斯死于热病。")],
    )
    graph.communities["community_001"] = CommunityNode(
        community_id="community_001",
        label="何塞/西班牙大帆船",
        community_name="community_1",
        keywords=["何塞", "西班牙大帆船"],
        retrieval_text="community 何塞 西班牙大帆船",
        local_summary="探索与发现",
        summary="探索与发现",
        entity_ids=["entity_jose", "entity_ship"],
        episode_ids=["episode_001_001"],
        relation_ids=["edge_discovered_ship"],
        chapter_start=1,
        chapter_end=1,
    )
    graph.sagas["saga_001"] = SagaNode(
        saga_id="saga_001",
        label="探索弧",
        arc_type="chapter_span_arc",
        key_entities=["何塞·阿尔卡蒂奥·布恩迪亚"],
        retrieval_text="saga 何塞 探索",
        episode_ids=["episode_001_001"],
        entity_ids=["entity_jose"],
        relation_ids=["edge_discovered_ship"],
        chapter_start=1,
        chapter_end=1,
        chapter_range=(1, 1),
        summary="围绕何塞探索世界的叙事弧。",
    )
    return graph


def test_retrieve_respects_visible_window() -> None:
    result = TemporalGraphRetriever().retrieve(
        _build_graph(),
        GraphQuery(query="何塞 大帆船", max_chapter=1, top_k=4, window_mode="visible"),
    )
    assert all((hit.chapter_index or 0) <= 1 for hit in result.hits)
    assert result.structured_context["visible_facts"]


def test_recent_window_limits_episodes() -> None:
    result = TemporalGraphRetriever().retrieve(
        _build_graph(),
        GraphQuery(query="梅尔基亚德斯", max_chapter=2, top_k=4, window_mode="recent", recent_episode_count=1),
    )
    assert result.visible_episode_count == 1
    assert all(hit.chapter_index in {1, 2, None} for hit in result.hits)
    assert "search_counts" in result.retrieval_trace
