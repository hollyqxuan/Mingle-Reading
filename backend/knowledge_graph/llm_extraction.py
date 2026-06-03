from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field

from backend.api.schemas import BookChunk
from backend.agents.celebrity.model_client import invoke_openai_compatible_messages


GraphEntityType = Literal["character", "location", "artifact", "group", "theme", "concept", "unknown"]
GraphRelationDirectionality = Literal["directed", "undirected"]

ALLOWED_ENTITY_TYPES: set[str] = {"character", "location", "artifact", "group", "theme", "concept", "unknown"}
ALLOWED_DIRECTIONALITIES: set[str] = {"directed", "undirected"}
ALLOWED_STATE_FAMILIES: set[str] = {
    "context",
    "location",
    "membership",
    "interaction",
    "sentiment",
    "identity",
    "status",
    "theme",
}

GRAPH_EXTRACTION_JSON_SCHEMA: dict[str, Any] = {
    "type": "json_schema",
    "json_schema": {
        "name": "graphiti_episode_extraction",
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "entities": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "canonical_name": {"type": "string"},
                            "entity_type": {"type": "string"},
                            "aliases": {"type": "array", "items": {"type": "string"}},
                            "resolution_hint": {"type": "string"},
                            "evidence": {"type": "string"},
                            "confidence": {"type": "number"},
                        },
                        "required": [
                            "canonical_name",
                            "entity_type",
                            "aliases",
                            "resolution_hint",
                            "evidence",
                            "confidence",
                        ],
                    },
                },
                "facts": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "source": {"type": "string"},
                            "target": {"type": "string"},
                            "relation_type": {"type": "string"},
                            "state_family": {"type": "string"},
                            "directionality": {"type": "string"},
                            "fact": {"type": "string"},
                            "evidence": {"type": "string"},
                            "confidence": {"type": "number"},
                        },
                        "required": [
                            "source",
                            "target",
                            "relation_type",
                            "state_family",
                            "directionality",
                            "fact",
                            "evidence",
                            "confidence",
                        ],
                    },
                },
            },
            "required": ["entities", "facts"],
        },
    },
}

GRAPH_EXTRACTION_JSON_OBJECT: dict[str, Any] = {"type": "json_object"}


@dataclass(slots=True)
class GraphExtractorRuntime:
    api_key: str
    base_url: str
    model_name: str
    provider_label: str


class KnownEntityCandidate(BaseModel):
    entity_id: str
    canonical_name: str
    entity_type: str
    aliases: list[str] = Field(default_factory=list)
    mention_count: int = 0
    last_seen_chapter: int = 0
    last_seen_paragraph: int = 0
    summary: str = ""
    generation: int = 0


class ExtractedEntityCandidate(BaseModel):
    canonical_name: str
    entity_type: GraphEntityType = "unknown"
    aliases: list[str] = Field(default_factory=list)
    resolution_hint: str = ""
    evidence: str = ""
    confidence: float = 0.0


class ExtractedFactCandidate(BaseModel):
    source: str
    target: str
    relation_type: str
    state_family: str = "context"
    directionality: GraphRelationDirectionality = "undirected"
    fact: str
    evidence: str = ""
    confidence: float = 0.0


class EpisodeGraphExtraction(BaseModel):
    entities: list[ExtractedEntityCandidate] = Field(default_factory=list)
    facts: list[ExtractedFactCandidate] = Field(default_factory=list)
    extraction_mode: Literal["llm-assisted"] = "llm-assisted"
    provider_label: str = ""
    raw_response: str = ""


def resolve_graph_extractor_runtime() -> GraphExtractorRuntime | None:
    if os.getenv("PYTEST_CURRENT_TEST"):
        return None
    if os.getenv("GRAPHITI_EXTRACTOR_DISABLE", "").strip().lower() in {"1", "true", "yes"}:
        return None
    candidates = [
        (
            "GRAPHITI_EXTRACTOR_API_KEY",
            "GRAPHITI_EXTRACTOR_BASE_URL",
            "GRAPHITI_EXTRACTOR_MODEL_NAME",
            "graphiti-extractor",
        ),
        (
            "MUSE_NEUTRAL_API_KEY",
            "MUSE_NEUTRAL_BASE_URL",
            "MUSE_NEUTRAL_MODEL_NAME",
            "neutral-fallback",
        ),
    ]
    for api_key_var, base_url_var, model_name_var, label in candidates:
        api_key = os.getenv(api_key_var, "").strip()
        base_url = os.getenv(base_url_var, "").strip()
        model_name = os.getenv(model_name_var, "").strip()
        if api_key and base_url and model_name:
            return GraphExtractorRuntime(
                api_key=api_key,
                base_url=base_url,
                model_name=model_name,
                provider_label=label,
            )
    return None


def extract_episode_graph_with_llm(
    *,
    runtime: GraphExtractorRuntime,
    chunk: BookChunk,
    known_entities: list[KnownEntityCandidate],
    recent_episode_contexts: list[str],
    timeout_seconds: int = 90,
) -> EpisodeGraphExtraction:
    system_prompt = _build_system_prompt()
    user_prompt = _build_user_prompt(
        chunk=chunk,
        known_entities=known_entities,
        recent_episode_contexts=recent_episode_contexts,
    )
    latest_raw = ""
    for attempt in range(1, 4):
        raw = _invoke_with_response_format_fallback(
            runtime=runtime,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            timeout_seconds=timeout_seconds,
            response_format=GRAPH_EXTRACTION_JSON_SCHEMA,
        )
        latest_raw = raw
        payload = _parse_json_payload(raw)
        if payload is None:
            raw = _repair_non_json_output(
                runtime=runtime,
                invalid_output=raw,
                timeout_seconds=timeout_seconds,
            )
            latest_raw = raw
            payload = _parse_json_payload(raw)
        if payload is not None:
            extraction = EpisodeGraphExtraction(
                entities=_coerce_entities(payload.get("entities", [])),
                facts=_coerce_facts(payload.get("facts", [])),
                extraction_mode="llm-assisted",
                provider_label=runtime.provider_label,
                raw_response=raw,
            )
            return extraction
        if attempt < 3:
            time.sleep(min(1.5, 0.35 * attempt))
    raise ValueError(f"llm extraction returned non-JSON content: {_clip_text(latest_raw)}")


def _build_system_prompt() -> str:
    return "\n".join(
        [
            "你是一个中文文学知识图谱抽取器。从小说段落中提取实体和关系。",
            "",
            "【核心规则——人物消歧】",
            "这部小说中，不同世代的角色可能共享相同的名字。你不能仅凭名字判断是否为同一人物。",
            "必须根据以下叙事信号判断本段提及的人物是新人还是已知人物：",
            "",
            "信号1【年龄/世代】：文中出现明确的年龄描述（如“十四岁”“年迈”“年轻时”），",
            "  若该年龄与已知实体的世代矛盾，则这是一个新角色。",
            "信号2【出生/登场】：文中描述某人诞生、首次出现、或从远方归来成为新人物。",
            "信号3【亲属称谓在描述不同人】：文中说“A是B的大儿子”——A和B是两个不同的人。",
            "  “父亲”“儿子”“哥哥”等称谓描述的是关系，不是同一个人。",
            "  绝不要把“大儿子”当作父亲实体的一条别名——这是一个新的独立角色。",
            "信号4【外貌/体格】：文中描述的具体外貌（「脑袋四方」「头发粗硬」「巨汉」）",
            "  如果与已知角色的描述矛盾，则是不同的人。",
            "信号5【角色/行为】：不同角色有不同的职业、行为模式、社会关系。",
            "  例如“炼金术士”“斗鸡场常客”“远征的士兵”描述的是不同的人。",
            "",
            "【消歧推理步骤】",
            "在判断当前段落中的某个角色是否与已知实体为同一人时，请按以下步骤推理：",
            "步骤1【特征提取】：列出此人在当前文本中的关键特征——年龄描述、具体行为、亲属称谓、外貌描写。",
            "步骤2【对照排查】：将上述特征逐一对照已知实体列表中的每个同名或相似实体。",
            "步骤3【排除不可能】：若已知实体在年龄/世代/行为/亲属关系方面与当前特征矛盾，则排除。",
            "步骤4【结论判断】：若排除所有已知实体后无法匹配，创建新实体（宁分不合）；若恰有一个匹配且无矛盾，使用已有实体名并在 resolution_hint 中说明理由。",
            "",
            "【canonical_name 命名规范】",
            "- 优先使用全名（如“何塞·阿尔卡蒂奥·布恩迪亚”而非“何塞·阿尔卡蒂奥”），全名更具区分度。",
            "- 如果原文只给了名字而未给姓氏，在 resolution_hint 中标注区分信息。",
            "- 不要使用亲属称谓作为 canonical_name（不要创建名为“父亲”或“大儿子”的实体）。",
            "- aliases 中只放同一个人在不同语境下的称呼，不要放入指向其他角色的关系描述。",
            "",
            "【其余规则】",
            "1. 重点提取 facts（关系），entities 只提取本段新出现的（最多8个）。",
            "2. 忽略所有英文注释、脚注、括号里的英文译名。",
            "3. 返回 JSON：{\"entities\":[...],\"facts\":[...]}，不要用 markdown 代码块。",
            "4. 无可靠抽取时返回 {\"entities\":[],\"facts\":[]}。不确定则省略。",
            "5. 当你无法确定某角色是否与已知实体为同一人时，倾向于创建新实体（宁分不合）。",
            "",
            "entity_type 必须严格区分:",
            "- character 人物（有名字或明确身份的人，不包括动物和物品）",
            "- location  地点（城镇、建筑、房间、河流等）",
            "- group     团体/组织/家族",
            "- artifact  物品/器物/动物",
            "- concept   概念/主题/事件",
            "",
            "relation_type 及 state_family:",
            "- FAMILY_OF/identity      亲属/血缘/婚姻",
            "- LOCATED_IN/location     位于某地",
            "- SPOKE_WITH/interaction  对话/交流",
            "- CONFLICTS_WITH/interaction 冲突/对抗",
            "- CARES_ABOUT/sentiment   关心/爱慕/牵挂",
            "- MEMBER_OF/membership    属于某团体",
            "- ACCOMPANIES/context     同行/陪伴",
            "- OWNS/status             拥有/使用",
            "",
            "directionality: directed (有方向) 或 undirected (双向)",
            "",
            "entity JSON 字段: canonical_name(必填), entity_type(必填，无把握选unknown), aliases, resolution_hint, evidence, confidence",
            "entity_type 没有默认值——每一条都必须根据原文内容主动判断类型",
            "fact JSON 字段: source, target, relation_type, state_family, directionality, fact, evidence, confidence",
        ]
    )


def _build_user_prompt(
    *,
    chunk: BookChunk,
    known_entities: list[KnownEntityCandidate],
    recent_episode_contexts: list[str],
) -> str:
    known_entity_lines = [
        (
            f"- {item.entity_id} | {item.canonical_name} | type={item.entity_type} "
            f"| aliases={','.join(item.aliases[:5])} | mentions={item.mention_count} "
            f"| last_seen=c{item.last_seen_chapter}/p{item.last_seen_paragraph}"
        )
        for item in known_entities[:25]
    ]
    recent_context_block = "\n".join(f"- {text}" for text in recent_episode_contexts[-3:] if text.strip())
    metadata = {
        "book_id": chunk.book_id,
        "chapter_id": chunk.chapter_id,
        "chapter_index": chunk.chapter_index,
        "paragraph_id": chunk.paragraph_id,
        "paragraph_index": chunk.paragraph_index,
    }
    return "\n".join(
        [
            "当前片段元数据：",
            json.dumps(metadata, ensure_ascii=False, indent=2),
            "",
            "前文上下文（最近3个片段）：",
            recent_context_block or "- 无",
            "",
            "已知实体列表（已在上文中出现）：",
            "\n".join(known_entity_lines) or "- 无",
            "",
            "当前片段文本（仅提取中文实体和关系，忽略其中的英文注释）：",
            chunk.text,
            "",
            "抽取要求：",
            "1. 将同一实体的不同称呼归一化为稳定的 canonical_name（优先使用最完整的全名形式）。",
            "2. 若提及的人物已存在于已知实体列表，优先使用已有实体名。",
            "3. aliases 只保留简短、精确的别名（昵称、简称），不要把亲属关系描述当作别名。",
            "4. resolution_hint 字段必填——用一句话说明你是如何判断该实体与已知实体的关系：",
            "   - 如果是已有实体：说明为什么确定为同一个人。",
            "   - 如果是新实体：说明为什么判断为不同的人（年龄、世代、外貌、行为等差异）。",
            "5. 忽略括号中的英文译名和英文脚注。",
            "6. 只返回 JSON。",
        ]
    )


def _strip_fences(raw: str) -> str:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _parse_json_payload(raw: str) -> dict[str, Any]:
    cleaned = _strip_fences(raw)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        try:
            return json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError:
            return None


def _repair_non_json_output(
    *,
    runtime: GraphExtractorRuntime,
    invalid_output: str,
    timeout_seconds: int,
) -> str:
    repair_system_prompt = "\n".join(
        [
            "You are a JSON repair assistant.",
            "Convert the given extraction output into strict JSON only.",
            'Target schema: {"entities":[...],"facts":[...]}',
            "Do not add markdown fences, commentary, or explanations.",
            'If the content cannot be recovered, return {"entities":[],"facts":[]}.',
        ]
    )
    repair_user_prompt = "\n".join(
        [
            "Rewrite the following content into valid JSON matching the required schema.",
            "Return JSON only.",
            "",
            invalid_output,
        ]
    )
    return _invoke_with_response_format_fallback(
        runtime=runtime,
        messages=[
            {"role": "system", "content": repair_system_prompt},
            {"role": "user", "content": repair_user_prompt},
        ],
        timeout_seconds=timeout_seconds,
        response_format=GRAPH_EXTRACTION_JSON_OBJECT,
    )


def _invoke_with_response_format_fallback(
    *,
    runtime: GraphExtractorRuntime,
    messages: list[dict[str, str]],
    timeout_seconds: int,
    response_format: dict[str, Any],
) -> str:
    try:
        return _invoke_with_retries(
            runtime=runtime,
            messages=messages,
            timeout_seconds=timeout_seconds,
            response_format=response_format,
        )
    except RuntimeError as exc:
        if not _is_response_format_unsupported(exc):
            raise
    return _invoke_with_retries(
        runtime=runtime,
        messages=messages,
        timeout_seconds=timeout_seconds,
        response_format=None,
    )


def _is_response_format_unsupported(exc: RuntimeError) -> bool:
    message = str(exc).lower()
    markers = [
        "response_format type is unavailable",
        "response_format is unavailable",
        "unsupported response_format",
        "response_format is not supported",
        "response format is unavailable",
        "response format is not supported",
    ]
    return any(marker in message for marker in markers)


def _invoke_with_retries(
    *,
    runtime: GraphExtractorRuntime,
    messages: list[dict[str, str]],
    timeout_seconds: int,
    response_format: dict[str, Any] | None,
    max_attempts: int = 3,
) -> str:
    last_exc: RuntimeError | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return invoke_openai_compatible_messages(
                api_key=runtime.api_key,
                base_url=runtime.base_url,
                model_name=runtime.model_name,
                messages=messages,
                temperature=0.0,
                max_tokens=8192,
                timeout_seconds=timeout_seconds,
                response_format=response_format,
            )
        except RuntimeError as exc:
            last_exc = exc
            if attempt >= max_attempts or not _is_retryable_transport_error(exc):
                raise
            time.sleep(min(2.0, 0.5 * attempt))
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("llm invocation failed without an explicit exception")


def _is_retryable_transport_error(exc: RuntimeError) -> bool:
    message = str(exc).lower()
    markers = [
        "network timeout",
        "timed out",
        "temporarily unavailable",
        "connection reset",
        "connection aborted",
        "connection refused",
        "remote end closed connection",
        "eof",
        "ssl",
        "unexpected",
    ]
    return any(marker in message for marker in markers)


def _coerce_entities(rows: list[Any]) -> list[ExtractedEntityCandidate]:
    entities: list[ExtractedEntityCandidate] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        canonical_name = str(row.get("canonical_name") or row.get("name") or "").strip()
        if not canonical_name:
            continue
        entity_type = str(row.get("entity_type") or "character").strip().lower()
        if entity_type not in ALLOWED_ENTITY_TYPES:
            entity_type = "unknown"
        aliases = [
            str(alias).strip()
            for alias in row.get("aliases", [])
            if isinstance(alias, str) and str(alias).strip()
        ]
        entities.append(
            ExtractedEntityCandidate(
                canonical_name=canonical_name,
                entity_type=entity_type,  # type: ignore[arg-type]
                aliases=aliases,
                resolution_hint=str(row.get("resolution_hint") or row.get("resolved_as") or "").strip(),
                evidence=str(row.get("evidence") or "").strip(),
                confidence=_coerce_confidence(row.get("confidence")),
            )
        )
    return entities


def _coerce_facts(rows: list[Any]) -> list[ExtractedFactCandidate]:
    facts: list[ExtractedFactCandidate] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        source = str(row.get("source") or "").strip()
        target = str(row.get("target") or "").strip()
        relation_type = str(row.get("relation_type") or "").strip()
        fact = str(row.get("fact") or "").strip()
        if not source or not target or not relation_type or not fact:
            continue
        state_family = str(row.get("state_family") or "context").strip().lower()
        if state_family not in ALLOWED_STATE_FAMILIES:
            state_family = "context"
        directionality = str(row.get("directionality") or "undirected").strip().lower()
        if directionality not in ALLOWED_DIRECTIONALITIES:
            directionality = "undirected"
        facts.append(
            ExtractedFactCandidate(
                source=source,
                target=target,
                relation_type=relation_type.upper(),
                state_family=state_family,
                directionality=directionality,  # type: ignore[arg-type]
                fact=fact,
                evidence=str(row.get("evidence") or "").strip(),
                confidence=_coerce_confidence(row.get("confidence")),
            )
        )
    return facts


def _coerce_confidence(value: Any) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(confidence, 1.0))


def extract_window_graph_with_llm(
    *,
    runtime: GraphExtractorRuntime,
    core_text: str,
    prev_context_text: str,
    chapter_index: int,
    known_entities: list[KnownEntityCandidate],
    family_tree_lines: list[str] | None = None,
    character_descriptions: list[str] | None = None,
    timeout_seconds: int = 30,
) -> EpisodeGraphExtraction:
    system_prompt = _build_window_system_prompt(
        family_tree_lines or [],
        character_descriptions=character_descriptions,
    )
    user_prompt = _build_window_user_prompt(
        core_text=core_text,
        prev_context_text=prev_context_text,
        chapter_index=chapter_index,
        known_entities=known_entities,
    )
    latest_raw = ""
    for attempt in range(1, 4):
        raw = _invoke_with_response_format_fallback(
            runtime=runtime,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            timeout_seconds=timeout_seconds,
            response_format=GRAPH_EXTRACTION_JSON_SCHEMA,
        )
        latest_raw = raw
        payload = _parse_json_payload(raw)
        if payload is None:
            raw = _repair_non_json_output(
                runtime=runtime,
                invalid_output=raw,
                timeout_seconds=timeout_seconds,
            )
            latest_raw = raw
            payload = _parse_json_payload(raw)
        if payload is not None:
            extraction = EpisodeGraphExtraction(
                entities=_coerce_entities(payload.get("entities", [])),
                facts=_coerce_facts(payload.get("facts", [])),
                extraction_mode="llm-assisted",
                provider_label=runtime.provider_label,
                raw_response=raw,
            )
            return extraction
        if attempt < 3:
            time.sleep(min(1.5, 0.35 * attempt))
    raise ValueError(f"llm extraction returned non-JSON content: {_clip_text(latest_raw)}")


def _build_window_system_prompt(family_tree_lines: list[str], character_descriptions: list[str] | None = None) -> str:
    honorific_rules = "\n".join(
        [
            "",
            "【尊称与昵称规则——关键】",
            "- “堂”“唐”是西班牙语 Don 的音译尊称，加在人名前面表示尊敬。",
            "  “堂何塞·阿尔卡蒂奥·布恩迪亚” = “何塞·阿尔卡蒂奥·布恩迪亚”，是同一个人。",
            "- “小”作为昵称前缀（如“小阿玛兰妲”），通常是同一人物的昵称变体，不是新角色。",
            "- 去除了尊称/昵称前缀后的名字，优先与已知实体合并（在 resolution_hint 中说明）。",
            "",
            "【canonical_name 命名规范补充】",
            "- 不要使用带尊称前缀的名字作为 canonical_name（用“阿波利纳尔·摩斯科特”而非“堂阿波利纳尔·摩斯科特”）。",
        ]
    )
    base = _build_system_prompt()
    base += honorific_rules
    if family_tree_lines:
        tree_section = "\n".join(
            [
                "",
                "【已知人物族谱——供消歧参考】",
                "以下是目前已建立的人物血缘/婚姻关系，帮助你判断新出现名字的归属：",
                *[f"  {line}" for line in family_tree_lines],
                "",
                "注意：族谱中的关系可能不完整。如果文中出现与族谱矛盾的信息，以原文为准。",
            ]
        )
        base += tree_section
    if character_descriptions:
        desc_section = "\n".join(
            [
                "",
                "【人物描述缓存】",
                "以下是已出现的高频角色描述，用于区分同名不同代的角色：",
                *character_descriptions,
                "",
            ]
        )
        base += desc_section
    return base


def _build_window_user_prompt(
    *,
    core_text: str,
    prev_context_text: str,
    chapter_index: int,
    known_entities: list[KnownEntityCandidate],
) -> str:
    known_entity_lines = []
    for item in known_entities[:30]:
        parts = [
            f"- {item.entity_id} | {item.canonical_name} | type={item.entity_type}",
        ]
        if item.summary:
            parts.append(f"desc=\"{item.summary[:100]}\"")
        if item.generation:
            parts.append(f"gen={item.generation}")
        parts.append(f"aliases={','.join(item.aliases[:5])}")
        parts.append(f"mentions={item.mention_count}")
        parts.append(f"last_seen=c{item.last_seen_chapter}/p{item.last_seen_paragraph}")
        known_entity_lines.append(" | ".join(parts))
    parts: list[str] = [
        f"当前章节: 第 {chapter_index} 章",
        "",
        "已知实体列表（已在上文中出现，供消歧参考）：",
        "\n".join(known_entity_lines) if known_entity_lines else "- 无",
    ]
    if prev_context_text.strip():
        parts.extend(
            [
                "",
                "=== 前文上下文（仅用于人物消歧，不要从中提取实体/关系） ===",
                prev_context_text,
                "=== 上下文结束 ===",
            ]
        )
    parts.extend(
        [
            "",
            "=== 当前文本（需要从中提取实体和关系） ===",
            core_text,
            "=== 当前文本结束 ===",
            "",
            "抽取要求：",
            "1. 仅从「当前文本」区域提取实体和关系。「前文上下文」仅用于辅助判断人物是否已存在。",
            "2. 将同一实体的不同称呼归一化为稳定的 canonical_name（优先使用最完整的全名形式，去除堂/小等前缀）。",
            "3. 若提及的人物已存在于已知实体列表，优先使用已有实体名，在 resolution_hint 中说明匹配理由。",
            "4. aliases 只保留简短、精确的别名（昵称、简称），不要把亲属关系描述当作别名。",
            "5. resolution_hint 字段必填——用一句话说明你是如何判断该实体与已知实体的关系：",
            "   - 如果是已有实体：说明为什么确定为同一个人。",
            "   - 如果是新实体：说明为什么判断为不同的人（年龄、世代、外貌、行为等差异）。",
            "6. 忽略括号中的英文译名和英文脚注。",
            "7. 只返回 JSON。",
        ]
    )
    return "\n".join(parts)


def _clip_text(text: str, limit: int = 280) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3] + "..."
