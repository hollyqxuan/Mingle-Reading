from __future__ import annotations

from backend.knowledge_graph.models import FactCandidate
from backend.knowledge_graph.relation_schema import build_state_slot, normalize_fact_candidate


def test_normalize_discovery_from_location_relation() -> None:
    normalized = normalize_fact_candidate(
        fact=FactCandidate(
            subject="何塞·阿尔卡蒂奥·布恩迪亚",
            predicate="LOCATED_IN",
            object="西班牙大帆船",
            relation_family="location",
            fact_text="何塞·阿尔卡蒂奥·布恩迪亚的远征队发现了一艘西班牙大帆船",
        ),
        raw_relation_type="LOCATED_IN",
        raw_state_family="location",
        raw_directionality="directed",
        target_entity_type="artifact",
    )
    assert normalized.relation_type == "DISCOVERED"
    assert "mapped_discovery_event_from_state_relation" in normalized.normalization_notes or "artifact_target_not_valid_for_stable_location" in normalized.normalization_notes


def test_normalize_death_and_body_disposal_relations() -> None:
    death = normalize_fact_candidate(
        fact=FactCandidate(
            subject="梅尔基亚德斯",
            predicate="LOCATED_IN",
            object="新加坡",
            relation_family="location",
            fact_text="梅尔基亚德斯在新加坡的沙洲上死于热病",
        ),
        raw_relation_type="LOCATED_IN",
        raw_state_family="location",
        raw_directionality="directed",
        target_entity_type="location",
    )
    disposal = normalize_fact_candidate(
        fact=FactCandidate(
            subject="梅尔基亚德斯",
            predicate="LOCATED_IN",
            object="爪哇海",
            relation_family="location",
            fact_text="梅尔基亚德斯的尸体被丢到了爪哇海的最深处",
        ),
        raw_relation_type="LOCATED_IN",
        raw_state_family="location",
        raw_directionality="directed",
        target_entity_type="location",
    )
    assert death.relation_type == "DIED_AT"
    assert disposal.relation_type == "BODY_DISPOSED_AT"


def test_build_state_slot_respects_directionality() -> None:
    directed = build_state_slot(
        state_family="location",
        source_entity_id="entity_a",
        directionality="directed",
        target_entity_id="entity_b",
        state_slot="location",
    )
    undirected = build_state_slot(
        state_family="membership",
        source_entity_id="entity_b",
        directionality="undirected",
        target_entity_id="entity_a",
        state_slot="membership",
    )
    assert directed == "location|entity_a"
    assert undirected == "membership|entity_a|entity_b"
