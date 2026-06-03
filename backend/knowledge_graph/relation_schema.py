from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field

from .models import FactCandidate, RelationDirectionality


RelationFamily = Literal["identity", "state", "eventive", "narrative", "interpretive", "context"]


class RelationSchemaEntry(BaseModel):
    relation_type: str
    relation_family: RelationFamily
    state_family: str = "context"
    directionality: RelationDirectionality = "undirected"
    state_slot: str | None = None
    aliases: list[str] = Field(default_factory=list)
    description: str = ""


class NormalizedFact(BaseModel):
    fact: FactCandidate
    relation_type: str
    relation_family: RelationFamily
    state_family: str
    directionality: RelationDirectionality
    state_slot: str | None = None
    normalization_notes: list[str] = Field(default_factory=list)


RELATION_SCHEMA_REGISTRY: dict[str, RelationSchemaEntry] = {
    "FAMILY_OF": RelationSchemaEntry(
        relation_type="FAMILY_OF",
        relation_family="identity",
        state_family="identity",
        directionality="undirected",
        aliases=["KIN_OF", "MARRIED_TO", "PARENT_OF", "CHILD_OF"],
        description="Family, kinship, or marriage relation.",
    ),
    "LOCATED_IN": RelationSchemaEntry(
        relation_type="LOCATED_IN",
        relation_family="state",
        state_family="location",
        directionality="directed",
        state_slot="location",
        aliases=["LIVES_IN", "STAYS_IN", "RESIDES_IN"],
        description="Stable or ongoing location relation.",
    ),
    "MEMBER_OF": RelationSchemaEntry(
        relation_type="MEMBER_OF",
        relation_family="state",
        state_family="membership",
        directionality="directed",
        state_slot="membership",
        aliases=["BELONGS_TO"],
        description="Stable membership or affiliation relation.",
    ),
    "OWNS": RelationSchemaEntry(
        relation_type="OWNS",
        relation_family="state",
        state_family="status",
        directionality="directed",
        state_slot="ownership",
        aliases=["HAS", "USES"],
        description="Stable ownership or possession relation.",
    ),
    "SPOKE_WITH": RelationSchemaEntry(
        relation_type="SPOKE_WITH",
        relation_family="eventive",
        state_family="interaction",
        directionality="undirected",
        aliases=["TALKED_TO", "CONVERSED_WITH"],
        description="Conversation or dialogue event.",
    ),
    "CONFLICTS_WITH": RelationSchemaEntry(
        relation_type="CONFLICTS_WITH",
        relation_family="eventive",
        state_family="interaction",
        directionality="undirected",
        aliases=["FOUGHT_WITH", "OPPOSES"],
        description="Conflict or antagonistic interaction.",
    ),
    "CARES_ABOUT": RelationSchemaEntry(
        relation_type="CARES_ABOUT",
        relation_family="interpretive",
        state_family="sentiment",
        directionality="directed",
        aliases=["LOVES", "WORRIES_ABOUT"],
        description="Emotional care, attachment, or concern.",
    ),
    "ACCOMPANIES": RelationSchemaEntry(
        relation_type="ACCOMPANIES",
        relation_family="eventive",
        state_family="context",
        directionality="undirected",
        aliases=["TRAVELS_WITH", "GOES_WITH"],
        description="Accompaniment within an event or scene.",
    ),
    "DISCOVERED": RelationSchemaEntry(
        relation_type="DISCOVERED",
        relation_family="eventive",
        state_family="context",
        directionality="directed",
        aliases=["FOUND", "UNEARTHED", "ENCOUNTERED"],
        description="Discovery or encounter event.",
    ),
    "DIED_AT": RelationSchemaEntry(
        relation_type="DIED_AT",
        relation_family="eventive",
        state_family="context",
        directionality="directed",
        aliases=["DIED_IN"],
        description="Death event with location target.",
    ),
    "BODY_DISPOSED_AT": RelationSchemaEntry(
        relation_type="BODY_DISPOSED_AT",
        relation_family="eventive",
        state_family="context",
        directionality="directed",
        aliases=["BURIED_AT", "CAST_INTO"],
        description="Body disposal or burial event.",
    ),
    "LEADS_COMMUNITY": RelationSchemaEntry(
        relation_type="LEADS_COMMUNITY",
        relation_family="interpretive",
        state_family="membership",
        directionality="directed",
        aliases=["GUIDES", "ORGANIZES"],
        description="Leadership role within a local collective.",
    ),
}


_ALIAS_TO_CANONICAL: dict[str, str] = {}
for canonical, entry in RELATION_SCHEMA_REGISTRY.items():
    _ALIAS_TO_CANONICAL[canonical] = canonical
    for alias in entry.aliases:
        _ALIAS_TO_CANONICAL[alias.upper()] = canonical


_DISCOVERY_MARKERS = ("发现", "发掘", "挖出", "挖掘出", "找到", "见到", "看见", "赫然停着", "unearth", "found")
_DEATH_MARKERS = ("死于", "死在", "病死", "热病", "died", "death")
_BODY_DISPOSAL_MARKERS = ("尸体被丢", "被丢到", "被丢到了", "埋在", "葬在", "body was thrown", "cast into")
_LEADERSHIP_MARKERS = ("族长式人物", "指导村社事务", "指导人们", "规划了街道", "leader", "guided")


def relation_entry_for(relation_type: str) -> RelationSchemaEntry:
    canonical = _ALIAS_TO_CANONICAL.get(relation_type.upper().strip(), relation_type.upper().strip())
    return RELATION_SCHEMA_REGISTRY.get(
        canonical,
        RelationSchemaEntry(
            relation_type=canonical,
            relation_family="context",
            state_family="context",
            directionality="undirected",
            description="Fallback relation schema.",
        ),
    )


def normalize_fact_candidate(
    *,
    fact: FactCandidate,
    raw_relation_type: str,
    raw_state_family: str,
    raw_directionality: RelationDirectionality,
    target_entity_type: str,
) -> NormalizedFact:
    notes: list[str] = []
    entry = relation_entry_for(raw_relation_type)
    relation_type = entry.relation_type
    relation_family = entry.relation_family
    state_family = entry.state_family if raw_state_family == "context" else raw_state_family
    directionality = raw_directionality or entry.directionality

    fact_text = fact.fact_text.strip()
    lower_fact = fact_text.lower()

    if relation_type in {"LOCATED_IN", "OWNS"} and _contains_any(fact_text, _DISCOVERY_MARKERS):
        relation_type = "DISCOVERED"
        entry = RELATION_SCHEMA_REGISTRY[relation_type]
        relation_family = entry.relation_family
        state_family = entry.state_family
        directionality = entry.directionality
        notes.append("mapped_discovery_event_from_state_relation")

    if relation_type == "LOCATED_IN" and _contains_any(fact_text, _DEATH_MARKERS):
        relation_type = "DIED_AT"
        entry = RELATION_SCHEMA_REGISTRY[relation_type]
        relation_family = entry.relation_family
        state_family = entry.state_family
        directionality = entry.directionality
        notes.append("mapped_death_event_from_location_relation")

    if relation_type == "LOCATED_IN" and _contains_any(fact_text, _BODY_DISPOSAL_MARKERS):
        relation_type = "BODY_DISPOSED_AT"
        entry = RELATION_SCHEMA_REGISTRY[relation_type]
        relation_family = entry.relation_family
        state_family = entry.state_family
        directionality = entry.directionality
        notes.append("mapped_body_disposal_event_from_location_relation")

    if relation_type == "MEMBER_OF" and _contains_any(fact_text, _LEADERSHIP_MARKERS):
        relation_type = "LEADS_COMMUNITY"
        entry = RELATION_SCHEMA_REGISTRY[relation_type]
        relation_family = entry.relation_family
        state_family = entry.state_family
        directionality = entry.directionality
        notes.append("mapped_leadership_relation_from_membership")

    if relation_type == "LOCATED_IN" and target_entity_type == "artifact":
        relation_type = "DISCOVERED"
        entry = RELATION_SCHEMA_REGISTRY[relation_type]
        relation_family = entry.relation_family
        state_family = entry.state_family
        directionality = entry.directionality
        notes.append("artifact_target_not_valid_for_stable_location")

    return NormalizedFact(
        fact=fact,
        relation_type=relation_type,
        relation_family=relation_family,
        state_family=state_family,
        directionality=directionality,
        state_slot=entry.state_slot,
        normalization_notes=notes,
    )


def build_state_slot(
    *,
    state_family: str,
    source_entity_id: str,
    directionality: RelationDirectionality,
    target_entity_id: str,
    state_slot: str | None,
) -> str:
    if not state_slot:
        return ""
    if directionality == "undirected":
        ordered = sorted((source_entity_id, target_entity_id))
        return f"{state_slot}|{ordered[0]}|{ordered[1]}"
    return f"{state_slot}|{source_entity_id}"


def _contains_any(text: str, markers: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(marker in text or marker in lowered for marker in markers)
