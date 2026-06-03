from __future__ import annotations

from fastapi.testclient import TestClient

from backend.api import app as app_module
from backend.knowledge_graph.models import EntityNode, GraphProvenance, RelationEdge, TemporalContextGraph


def _graph_for_audit() -> TemporalContextGraph:
    graph = TemporalContextGraph(book_id="demo-book", title="Demo")
    graph.entities["entity_a"] = EntityNode(
        entity_id="entity_a",
        canonical_name="何塞·阿尔卡蒂奥·布恩迪亚",
        entity_type="character",
        mention_count=1,
        first_seen_chapter=1,
        first_seen_paragraph=1,
        last_seen_chapter=1,
        last_seen_paragraph=1,
    )
    graph.entities["entity_b"] = EntityNode(
        entity_id="entity_b",
        canonical_name="西班牙大帆船",
        entity_type="artifact",
        mention_count=1,
        first_seen_chapter=1,
        first_seen_paragraph=1,
        last_seen_chapter=1,
        last_seen_paragraph=1,
    )
    graph.relations["edge_1"] = RelationEdge(
        edge_id="edge_1",
        source_entity_id="entity_a",
        target_entity_id="entity_b",
        relation_type="DISCOVERED",
        state_family="context",
        directionality="directed",
        fact="远征队发现了一艘西班牙大帆船",
        fact_signature="DISCOVERED|context|entity_a|entity_b",
        valid_at_chapter=1,
        valid_at_paragraph=1,
        created_at="2026-01-01T00:00:00Z",
        episode_ids=["episode_001_001"],
        metadata={"normalization_notes": ["mapped_discovery_event_from_state_relation"]},
        provenance=[
            GraphProvenance(
                chunk_id="chunk-1",
                book_id="demo-book",
                chapter_id="chapter-001",
                chapter_index=1,
                paragraph_id="paragraph-001",
                paragraph_index=1,
                text_excerpt="发现大帆船",
            )
        ],
    )
    return graph


def test_graph_audit_relation_endpoint(monkeypatch) -> None:
    monkeypatch.setattr(app_module, "get_or_build_graph", lambda book_id: _graph_for_audit())
    client = TestClient(app_module.app)
    response = client.get("/api/books/demo-book/graph/audit", params={"relation_id": "edge_1"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["relation"]["edge_id"] == "edge_1"
    assert payload["story_timeline"]["tvalid_start_chapter"] == 1
    assert payload["provenance"][0]["chunk_id"] == "chunk-1"
