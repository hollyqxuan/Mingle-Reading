from __future__ import annotations

import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from backend.config import ROOT_DIR
from backend.api.schemas import (
    ChatMessage,
    PersonaAgentConfig,
    PersonaAgentStatus,
    PersonaCatalogSummary,
    PersonaKnowledgeBundle,
    PersonaProfile,
    PersonaPromptPreview,
    PersonaPromptPreviewRequest,
    PersonaPromptTraits,
    PersonaRAGHit,
    PersonaRAGQueryRequest,
)
from backend.agents.celebrity.model_client import invoke_openai_compatible_messages


class PersonaAgentConfigurationError(RuntimeError):
    """Raised when a persona agent is missing runtime configuration."""


class PersonaAgentInvocationError(RuntimeError):
    """Raised when a persona agent call fails upstream."""


def _default_prompt_traits() -> PersonaPromptTraits:
    return PersonaPromptTraits(
        system_role="中性中文阅读陪伴 agent",
        opening_instruction="先贴近当前已读文本，再回答问题；如果证据不足，直接说明不足。",
        tone_keywords=["清晰", "克制", "贴近文本"],
        reasoning_steps=[
            "先概括当前可见文本在说什么。",
            "再指出和问题最相关的细节与关系。",
            "最后给出不超出已读范围的判断。",
        ],
        forbidden_patterns=[
            "不要泄露未来剧情。",
            "不要假装看过用户尚未读到的内容。",
            "不要编造原文没有出现的引文或事实。",
        ],
        response_policies=[
            "优先引用当前可见正文证据。",
            "无法确定时明确说证据不足。",
            "保持简洁，不铺陈无关背景。",
        ],
    )


CELEBRITY_SKILL_ROOT = ROOT_DIR / "backend" / "assets" / "Celebrity-skill" / "Celebrity-skill"


AGENT_CONFIGS: dict[str, PersonaAgentConfig] = {
    "neutral": PersonaAgentConfig(
        agent_id="neutral",
        persona_id="neutral",
        display_name="中性导读",
        language="zh-CN",
        api_key_env_var="MUSE_NEUTRAL_API_KEY",
        base_url_env_var="MUSE_NEUTRAL_BASE_URL",
        model_name_env_var="MUSE_NEUTRAL_MODEL_NAME",
        default_model_name="",
        prompt_traits=_default_prompt_traits(),
    ),
    "lu-xun": PersonaAgentConfig(
        agent_id="lu-xun",
        persona_id="persona_lu_xun",
        display_name="鲁迅",
        language="zh-CN",
        api_key_env_var="LU_XUN_API_KEY",
        base_url_env_var="LU_XUN_BASE_URL",
        model_name_env_var="LU_XUN_MODEL_NAME",
        default_model_name="",
        persona_pack_path="backend/assets/Celebrity-skill/Celebrity-skill/LuXun-skill-main/SKILL.md",
        catalog_path="backend/assets/Celebrity-skill/Celebrity-skill/LuXun-skill-main/references/research",
        prompt_traits=PersonaPromptTraits(
            system_role="鲁迅式中文阅读陪伴 agent，以冷峻批判、社会病理剖析和去魅式阅读见长。",
            opening_instruction="先抓文本中的麻木、看客、遮蔽、自欺或权力结构，再给出锋利但不剧透的判断。",
            tone_keywords=["冷峻", "讽刺", "文白夹杂", "去魅", "批判"],
            reasoning_steps=[
                "先定位当前段落暴露出的现实处境与人物姿态。",
                "再追问其中的精神结构、看客心理或瞒与骗。",
                "最后回到具体字句，给出不越界的批评结论。",
            ],
            forbidden_patterns=[
                "不要做温吞的鸡汤式安慰。",
                "不要空谈宏大道理而脱离文本。",
                "不要借全书视角直接泄露未来命运或反转。",
            ],
            response_policies=[
                "优先用文本细节支撑批评判断。",
                "可以尖锐，但必须基于当前可见内容。",
                "若证据不足，宁可停在追问，不要越界下结论。",
            ],
        ),
    ),
    "mark-twain": PersonaAgentConfig(
        agent_id="mark-twain",
        persona_id="persona_mark_twain",
        display_name="马克·吐温",
        language="zh-CN",
        api_key_env_var="MARK_TWAIN_API_KEY",
        base_url_env_var="MARK_TWAIN_BASE_URL",
        model_name_env_var="MARK_TWAIN_MODEL_NAME",
        default_model_name="",
        persona_pack_path="backend/assets/Celebrity-skill/Celebrity-skill/MarkTwain-skill-main/SKILL.md",
        catalog_path="backend/assets/Celebrity-skill/Celebrity-skill/MarkTwain-skill-main/references/research",
        prompt_traits=PersonaPromptTraits(
            system_role="马克·吐温式中文阅读陪伴 agent，以口语化叙述、幽默反讽和现实观察见长。",
            opening_instruction="先把场面讲活，再指出其中的荒诞、虚伪或社会偏见。",
            tone_keywords=["口语化", "机智", "幽默", "反讽", "现实观察"],
            reasoning_steps=[
                "先复述当前情境里最鲜活的人和事。",
                "再抓住其中不体面却真实的矛盾。",
                "最后用带笑意但不轻浮的方式点出判断。",
            ],
            forbidden_patterns=[
                "不要把幽默写成轻佻嬉闹。",
                "不要脱离文本大谈作者生平轶事。",
                "不要借全书视角提前揭露后文事件。",
            ],
            response_policies=[
                "优先保留叙事现场感。",
                "把讽刺建立在观察上，而不是纯态度。",
                "如果当前文本不足以支撑判断，就明确收住。",
            ],
        ),
    ),
    "zhang-ailing": PersonaAgentConfig(
        agent_id="zhang-ailing",
        persona_id="persona_zhang_ailing",
        display_name="张爱玲",
        language="zh-CN",
        api_key_env_var="ZHANG_AILING_API_KEY",
        base_url_env_var="ZHANG_AILING_BASE_URL",
        model_name_env_var="ZHANG_AILING_MODEL_NAME",
        default_model_name="",
        persona_pack_path="backend/assets/Celebrity-skill/Celebrity-skill/ZhangAiLing-skill-main/SKILL.md",
        catalog_path="backend/assets/Celebrity-skill/Celebrity-skill/ZhangAiLing-skill-main/references/research",
        prompt_traits=PersonaPromptTraits(
            system_role="张爱玲式中文阅读陪伴 agent，以苍凉美学、物质细节分析和关系心理透视见长。",
            opening_instruction="先看房间、衣饰、动作、语气这些细节，再推近人物关系与命运阴影。",
            tone_keywords=["克制", "苍凉", "精致", "冷静", "细节感"],
            reasoning_steps=[
                "先辨认场景中的物质细节和气氛。",
                "再分析人物之间的情感落差与权力位置。",
                "最后落回文本，让判断停在当前可见的分寸内。",
            ],
            forbidden_patterns=[
                "不要模仿受版权保护的原句。",
                "不要把审美判断写成空泛抒情。",
                "不要借全书信息提前透露人物后续命运。",
            ],
            response_policies=[
                "优先从细节进入关系判断。",
                "保持冷静克制，不写成戏剧化表演。",
                "如果当前证据不足，就只点出隐约趋势，不越界断言。",
            ],
        ),
    ),
}


PERSONA_ALIASES = {
    "neutral": "neutral",
    "lu-xun": "lu-xun",
    "persona_lu_xun": "lu-xun",
    "mark-twain": "mark-twain",
    "persona_mark_twain": "mark-twain",
    "eileen-chang": "zhang-ailing",
    "zhang-ailing": "zhang-ailing",
    "persona_zhang_ailing": "zhang-ailing",
}


RESEARCH_FILE_MAP: dict[str, tuple[str, str, float]] = {
    "01-writings.md": ("works", "writings", 1.2),
    "02-conversations.md": ("voice_sources", "conversations", 1.15),
    "03-expression-dna.md": ("voice_sources", "expression_dna", 1.25),
    "04-external-views.md": ("biography_and_critical", "external_views", 0.95),
    "05-decisions.md": ("biography_and_critical", "decisions", 1.1),
    "06-timeline.md": ("biography_and_critical", "timeline", 0.9),
}


@dataclass(frozen=True)
class CelebritySkillAsset:
    root_dir: Path
    skill_path: Path
    readme_path: Path
    research_dir: Path


def _canonical_agent_id(persona_id: str) -> str:
    return PERSONA_ALIASES.get(persona_id, "neutral")


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _tokenize(value: str) -> list[str]:
    return [token for token in re.split(r"[^a-zA-Z0-9\u4e00-\u9fff]+", value.lower()) if token]


def _score_snippet(query: str, row: dict[str, Any]) -> float:
    query_tokens = _tokenize(query)
    if not query_tokens:
        return 0.0
    retrieval_text = str(row.get("retrieval_text") or row.get("text") or "").lower()
    title = str(row.get("title") or "").lower()
    matched = 0.0
    for token in query_tokens:
        if token in retrieval_text:
            matched += 1.0
        elif token in title:
            matched += 0.5
    return matched * float(row.get("retrieval_weight", 1.0))


def _format_categories(categories: list[str]) -> str:
    values = [item for item in categories if item]
    return ", ".join(values) if values else "works / voice_sources / biography_and_critical / skill_rules / overview"


def _fallback_persona_hits(rows: list[dict[str, Any]], top_k: int) -> list[PersonaRAGHit]:
    category_priority = {
        "skill_rules": 4.0,
        "voice_sources": 3.0,
        "overview": 2.5,
        "works": 2.0,
        "biography_and_critical": 1.5,
    }
    ranked = sorted(
        rows,
        key=lambda row: (
            category_priority.get(str(row.get("source_category", "")), 1.0)
            * float(row.get("retrieval_weight", 1.0)),
            float(row.get("retrieval_weight", 1.0)),
        ),
        reverse=True,
    )
    return [
        PersonaRAGHit(
            snippet_id=str(row.get("snippet_id", "")),
            title=str(row.get("title", "")),
            source_category=str(row.get("source_category", "")),
            snippet_type=str(row.get("snippet_type", "")),
            text=str(row.get("text", "")),
            score=float(row.get("retrieval_weight", 1.0)),
            retrieval_weight=float(row.get("retrieval_weight", 1.0)),
        )
        for row in ranked[:top_k]
    ]


def _coerce_title(path: Path, fallback: str) -> str:
    text = _read_text(path)
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
    return fallback


def _split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---"):
        return {}, text
    lines = text.splitlines()
    if len(lines) < 3:
        return {}, text
    end_index = None
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            end_index = index
            break
    if end_index is None:
        return {}, text
    meta: dict[str, Any] = {}
    current_key: str | None = None
    list_buffer: list[str] = []
    for raw_line in lines[1:end_index]:
        line = raw_line.rstrip()
        if not line.strip():
            continue
        if line.lstrip().startswith("- ") and current_key:
            list_buffer.append(line.split("- ", 1)[1].strip())
            meta[current_key] = list_buffer
            continue
        if ":" in line:
            key, value = line.split(":", 1)
            current_key = key.strip()
            list_buffer = []
            cleaned = value.strip().strip('"')
            meta[current_key] = cleaned
    body = "\n".join(lines[end_index + 1 :]).strip()
    return meta, body


def _clean_markdown_text(text: str) -> str:
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1", text)
    text = re.sub(r"^>\s?", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\|.*\|$", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*[-*]\s+", "- ", text, flags=re.MULTILINE)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _build_snippet_chunks(text: str, max_chars: int = 780) -> list[str]:
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", _clean_markdown_text(text)) if part.strip()]
    if not paragraphs:
        return []
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        candidate = f"{current}\n\n{paragraph}".strip() if current else paragraph
        if len(candidate) <= max_chars:
            current = candidate
            continue
        if current:
            chunks.append(current)
        if len(paragraph) <= max_chars:
            current = paragraph
            continue
        sentence_parts = re.split(r"(?<=[。！？!?；;])", paragraph)
        temp = ""
        for sentence in sentence_parts:
            sentence = sentence.strip()
            if not sentence:
                continue
            candidate_sentence = f"{temp}{sentence}"
            if len(candidate_sentence) <= max_chars:
                temp = candidate_sentence
            else:
                if temp:
                    chunks.append(temp.strip())
                temp = sentence
        current = temp.strip()
    if current:
        chunks.append(current)
    return chunks


def _extract_markdown_sections(text: str, fallback_title: str) -> list[tuple[str, str]]:
    sections: list[tuple[str, str]] = []
    current_title = fallback_title
    buffer: list[str] = []
    for line in text.splitlines():
        heading = re.match(r"^(#{1,3})\s+(.+)$", line.strip())
        if heading:
            section_text = "\n".join(buffer).strip()
            if section_text:
                sections.append((current_title, section_text))
            current_title = heading.group(2).strip()
            buffer = []
            continue
        buffer.append(line)
    final_text = "\n".join(buffer).strip()
    if final_text:
        sections.append((current_title, final_text))
    return sections or [(fallback_title, text)]


def _build_skill_asset(config: PersonaAgentConfig) -> CelebritySkillAsset | None:
    if not config.persona_pack_path:
        return None
    skill_path = ROOT_DIR / config.persona_pack_path
    root_dir = skill_path.parent
    return CelebritySkillAsset(
        root_dir=root_dir,
        skill_path=skill_path,
        readme_path=root_dir / "README.md",
        research_dir=root_dir / "references" / "research",
    )


def _build_research_documents(asset: CelebritySkillAsset) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    documents: list[dict[str, Any]] = []
    snippets: list[dict[str, Any]] = []
    category_counts: dict[str, int] = {
        "works": 0,
        "voice_sources": 0,
        "biography_and_critical": 0,
        "skill_rules": 0,
        "overview": 0,
    }

    skill_text = _read_text(asset.skill_path)
    skill_meta, skill_body = _split_frontmatter(skill_text)
    readme_text = _read_text(asset.readme_path)

    documents.extend(
        [
            {
                "document_id": f"{asset.root_dir.name}-skill",
                "title": skill_meta.get("name") or asset.root_dir.name,
                "source_category": "skill_rules",
                "snippet_type": "skill_rules",
                "text": skill_body,
                "path": str(asset.skill_path.relative_to(ROOT_DIR)),
                "retrieval_weight": 1.35,
            },
            {
                "document_id": f"{asset.root_dir.name}-overview",
                "title": _coerce_title(asset.readme_path, f"{asset.root_dir.name} README"),
                "source_category": "overview",
                "snippet_type": "overview",
                "text": readme_text,
                "path": str(asset.readme_path.relative_to(ROOT_DIR)),
                "retrieval_weight": 1.1,
            },
        ]
    )

    for file_name, (category, snippet_type, weight) in RESEARCH_FILE_MAP.items():
        path = asset.research_dir / file_name
        if not path.exists():
            continue
        documents.append(
            {
                "document_id": f"{asset.root_dir.name}-{file_name}",
                "title": _coerce_title(path, file_name),
                "source_category": category,
                "snippet_type": snippet_type,
                "text": _read_text(path),
                "path": str(path.relative_to(ROOT_DIR)),
                "retrieval_weight": weight,
            }
        )

    for document in documents:
        category = str(document["source_category"])
        title = str(document["title"])
        text = str(document["text"])
        chunks: list[str] = []
        for section_title, section_text in _extract_markdown_sections(text, title):
            for chunk in _build_snippet_chunks(section_text):
                chunks.append(f"{section_title}\n{chunk}".strip())
        if not chunks and text.strip():
            chunks = _build_snippet_chunks(text)
        if category in category_counts:
            category_counts[category] += len(chunks)
        for index, chunk in enumerate(chunks, start=1):
            snippet_id = f"{document['document_id']}::s{index}"
            snippets.append(
                {
                    "snippet_id": snippet_id,
                    "document_id": document["document_id"],
                    "title": title,
                    "source_category": category,
                    "snippet_type": document["snippet_type"],
                    "text": chunk,
                    "retrieval_text": f"{title}\n{chunk}",
                    "retrieval_weight": float(document["retrieval_weight"]),
                    "path": document["path"],
                }
            )

    manifest = {
        "source": "celebrity-skill",
        "skill_root": str(asset.root_dir.relative_to(ROOT_DIR)),
        "files": {
            "skill": str(asset.skill_path.relative_to(ROOT_DIR)),
            "readme": str(asset.readme_path.relative_to(ROOT_DIR)),
            "research_dir": str(asset.research_dir.relative_to(ROOT_DIR)),
        },
        "document_counts": {
            "total_documents": len(documents),
            **category_counts,
        },
        "snippet_counts": {
            "total_snippets": len(snippets),
            **category_counts,
        },
        "categories": list(category_counts.keys()),
        "documents": [
            {
                "document_id": doc["document_id"],
                "title": doc["title"],
                "source_category": doc["source_category"],
                "path": doc["path"],
            }
            for doc in documents
        ],
        "skill_meta": skill_meta,
    }
    return documents, snippets, manifest


def _profile_citation_label(config: PersonaAgentConfig, manifest: dict[str, Any]) -> str:
    counts = manifest.get("document_counts", {})
    total_documents = int(counts.get("total_documents", 0) or 0)
    categories = {
        "style_profile": "风格画像",
        "voice_sources": "语言材料",
        "research_note": "研究笔记",
    }
    category_labels = [
        label
        for key, label in categories.items()
        if int(counts.get(key, 0) or 0) > 0
    ]
    basis = "、".join(category_labels) if category_labels else "本地资料包"
    if config.agent_id == "neutral":
        return "中性导读模式：不套用作家声腔，优先依据当前已读正文回答。"
    if total_documents:
        return f"基于本地{config.display_name}风格资料包（{basis}，{total_documents} 份资料）；回答仍以当前已读正文为准。"
    return f"基于本地{config.display_name}风格资料包；回答仍以当前已读正文为准。"


def _profile_from_skill_bundle(config: PersonaAgentConfig, manifest: dict[str, Any]) -> PersonaProfile:
    persona_type = "neutral" if config.agent_id == "neutral" else "literary_master"
    return PersonaProfile(
        persona_id=config.persona_id,
        name=config.display_name,
        source_type=persona_type,
        style_traits=config.prompt_traits.tone_keywords,
        reasoning_style=config.prompt_traits.reasoning_steps,
        citation=_profile_citation_label(config, manifest),
        prompt_scaffold=config.prompt_traits.response_policies,
    )


def _catalog_summary_from_manifest(manifest: dict[str, Any]) -> PersonaCatalogSummary:
    counts = manifest.get("snippet_counts", {})
    return PersonaCatalogSummary(
        total_sources=int(counts.get("total_snippets", 0)),
        works=int(counts.get("works", 0)),
        voice_sources=int(counts.get("voice_sources", 0)),
        biography_and_critical=int(counts.get("biography_and_critical", 0)),
    )


def _build_bundle(config: PersonaAgentConfig) -> tuple[PersonaKnowledgeBundle, dict[str, Any]]:
    if config.agent_id == "neutral":
        profile = PersonaProfile(
            persona_id=config.persona_id,
            name=config.display_name,
            source_type="neutral",
            style_traits=config.prompt_traits.tone_keywords,
            reasoning_style=config.prompt_traits.reasoning_steps,
            citation="Project neutral persona",
            prompt_scaffold=config.prompt_traits.response_policies,
        )
        bundle = PersonaKnowledgeBundle(
            config=config,
            profile=profile,
            catalog_summary=PersonaCatalogSummary(),
            persona_pack={"source": "neutral"},
            catalog={"source": "neutral"},
        )
        kb = {
            "manifest": {
                "persona_id": config.persona_id,
                "source": "neutral",
                "document_counts": {"total_documents": 0},
                "snippet_counts": {"total_snippets": 0},
                "categories": [],
            },
            "documents": [],
            "snippets": [],
        }
        return bundle, kb

    asset = _build_skill_asset(config)
    if asset is None:
        raise PersonaAgentConfigurationError(f"persona agent `{config.agent_id}` has no celebrity-skill asset path")
    documents, snippets, manifest = _build_research_documents(asset)
    skill_text = _read_text(asset.skill_path)
    skill_meta, skill_body = _split_frontmatter(skill_text)
    persona_pack = {
        "persona_id": config.persona_id,
        "display_name": config.display_name,
        "source": "celebrity-skill",
        "skill_meta": skill_meta,
        "skill_body": skill_body,
        "style_layer": {
            "tone_keywords": config.prompt_traits.tone_keywords,
            "reasoning_steps": config.prompt_traits.reasoning_steps,
        },
        "constraints": {
            "max_response_length": 380,
        },
        "source_layer": [
            {
                "citation": item.get("path"),
                "title": item.get("title"),
                "source_category": item.get("source_category"),
            }
            for item in manifest.get("documents", [])
        ],
    }
    catalog = {
        "source": "celebrity-skill",
        "skill_root": manifest.get("skill_root"),
        "documents": manifest.get("documents", []),
        "counts": manifest.get("snippet_counts", {}),
    }
    bundle = PersonaKnowledgeBundle(
        config=config,
        profile=_profile_from_skill_bundle(config, manifest),
        catalog_summary=_catalog_summary_from_manifest(manifest),
        persona_pack=persona_pack,
        catalog=catalog,
    )
    kb = {
        "manifest": {
            "persona_id": config.persona_id,
            "display_name": config.display_name,
            **manifest,
        },
        "documents": documents,
        "snippets": snippets,
    }
    return bundle, kb


@lru_cache(maxsize=1)
def _load_persona_state() -> dict[str, Any]:
    bundles: dict[str, PersonaKnowledgeBundle] = {}
    kb_index: dict[str, dict[str, Any]] = {}
    for agent_id, config in AGENT_CONFIGS.items():
        bundle, kb = _build_bundle(config)
        bundles[agent_id] = bundle
        kb_index[config.persona_id] = kb
    return {"bundles": bundles, "kb_index": kb_index}


def _load_knowledge_bundles() -> dict[str, PersonaKnowledgeBundle]:
    return _load_persona_state()["bundles"]


def _load_persona_kb_index() -> dict[str, dict[str, Any]]:
    return _load_persona_state()["kb_index"]


def list_personas() -> list[PersonaProfile]:
    return [bundle.profile for bundle in _load_knowledge_bundles().values()]


def list_persona_agents() -> list[PersonaAgentStatus]:
    statuses: list[PersonaAgentStatus] = []
    for agent_id, bundle in _load_knowledge_bundles().items():
        config = bundle.config
        resolved_base_url = os.getenv(config.base_url_env_var, config.default_base_url)
        resolved_model_name = os.getenv(config.model_name_env_var, config.default_model_name)
        statuses.append(
            PersonaAgentStatus(
                agent_id=agent_id,
                persona_id=config.persona_id,
                display_name=config.display_name,
                language=config.language,
                api_key_env_var=config.api_key_env_var,
                base_url_env_var=config.base_url_env_var,
                model_name_env_var=config.model_name_env_var,
                resolved_base_url=resolved_base_url,
                resolved_model_name=resolved_model_name,
                has_api_key=bool(os.getenv(config.api_key_env_var)),
                persona_pack_path=config.persona_pack_path,
                catalog_path=config.catalog_path,
                catalog_summary=bundle.catalog_summary,
                prompt_traits=config.prompt_traits,
            )
        )
    return statuses


def get_persona(persona_id: str) -> PersonaProfile:
    return _load_knowledge_bundles()[_canonical_agent_id(persona_id)].profile


def get_persona_agent(persona_id: str) -> PersonaAgentStatus:
    agent_id = _canonical_agent_id(persona_id)
    for status in list_persona_agents():
        if status.agent_id == agent_id:
            return status
    return list_persona_agents()[0]


def get_persona_knowledge_bundle(persona_id: str) -> PersonaKnowledgeBundle:
    return _load_knowledge_bundles()[_canonical_agent_id(persona_id)]


def get_persona_kb_manifest(persona_id: str) -> dict[str, Any]:
    bundle = get_persona_knowledge_bundle(persona_id)
    return _load_persona_kb_index().get(bundle.config.persona_id, {}).get("manifest", {})


def ensure_persona_assets(persona_id: str) -> None:
    bundle = get_persona_knowledge_bundle(persona_id)
    if bundle.config.agent_id == "neutral":
        return
    kb = _load_persona_kb_index().get(bundle.config.persona_id, {})
    missing = []
    if not bundle.persona_pack:
        missing.append("skill_bundle")
    if not bundle.catalog:
        missing.append("skill_catalog")
    if not kb.get("manifest"):
        missing.append("skill_manifest")
    if not kb.get("snippets"):
        missing.append("skill_snippets")
    if missing:
        raise PersonaAgentConfigurationError(
            f"persona agent `{bundle.config.agent_id}` is missing required celebrity-skill assets: {', '.join(missing)}"
        )


def retrieve_persona_snippets(persona_id: str, request: PersonaRAGQueryRequest) -> list[PersonaRAGHit]:
    bundle = get_persona_knowledge_bundle(persona_id)
    kb = _load_persona_kb_index().get(bundle.config.persona_id, {})
    rows = list(kb.get("snippets", []))
    categories = set(request.categories)
    if categories:
        rows = [row for row in rows if row.get("source_category") in categories]

    scored: list[PersonaRAGHit] = []
    for row in rows:
        score = _score_snippet(request.query, row)
        if score <= 0:
            continue
        scored.append(
            PersonaRAGHit(
                snippet_id=str(row.get("snippet_id", "")),
                title=str(row.get("title", "")),
                source_category=str(row.get("source_category", "")),
                snippet_type=str(row.get("snippet_type", "")),
                text=str(row.get("text", "")),
                score=score,
                retrieval_weight=float(row.get("retrieval_weight", 1.0)),
            )
        )
    scored.sort(key=lambda item: item.score, reverse=True)
    if scored:
        return scored[: request.top_k]
    return _fallback_persona_hits(rows, request.top_k)


def resolve_persona_runtime(persona_id: str) -> tuple[PersonaAgentConfig, str, str, str]:
    bundle = get_persona_knowledge_bundle(persona_id)
    config = bundle.config
    api_key = os.getenv(config.api_key_env_var, "").strip()
    base_url = os.getenv(config.base_url_env_var, config.default_base_url).strip()
    model_name = os.getenv(config.model_name_env_var, config.default_model_name).strip()

    missing = []
    if not api_key:
        missing.append(config.api_key_env_var)
    if not base_url:
        missing.append(config.base_url_env_var)
    if not model_name:
        missing.append(config.model_name_env_var)
    if missing:
        raise PersonaAgentConfigurationError(
            f"persona agent `{config.agent_id}` is not fully configured. Missing: {', '.join(missing)}"
        )
    return config, api_key, base_url, model_name


def build_persona_system_prompt(persona_id: str, task: str) -> str:
    bundle = get_persona_knowledge_bundle(persona_id)
    traits = bundle.config.prompt_traits
    pack = bundle.persona_pack
    manifest = get_persona_kb_manifest(persona_id)
    skill_meta = pack.get("skill_meta", {})
    skill_description = str(skill_meta.get("description", "")).strip()
    max_response_length = int(pack.get("constraints", {}).get("max_response_length", 380))
    source_count = manifest.get("snippet_counts", {}).get("total_snippets", 0)
    sections = [
        f"你是 {bundle.config.display_name} 的中文阅读陪伴 agent。",
        f"技能来源：Celebrity-skill 本地资料包，已接入研究文本 RAG 与书本知识图谱回答链。",
        f"角色定位：{traits.system_role}",
        f"适用说明：{skill_description or traits.opening_instruction}",
        f"语言与气质：{', '.join(traits.tone_keywords)}。",
        "方法步骤：",
        *[f"{index}. {step}" for index, step in enumerate(traits.reasoning_steps, start=1)],
        "禁止事项：",
        *[f"- {item}" for item in traits.forbidden_patterns],
        "回答策略：",
        *[f"- {item}" for item in traits.response_policies],
        f"资料规模：当前 persona 资料切成 {source_count} 条可检索片段。",
        "事实边界：你可以使用本 persona 的 celebrity-skill 资料作为风格与分析框架，但谈论这本书时只能依据当前请求提供的可见正文与知识图谱证据，不能向用户剧透。",
        f"输出长度：默认控制在 {max_response_length} 字左右；任务是 {task} 时，也遵守这个约束。",
    ]
    return "\n".join(section for section in sections if section)


def build_persona_prompt_preview(persona_id: str, request: PersonaPromptPreviewRequest) -> PersonaPromptPreview:
    bundle = get_persona_knowledge_bundle(persona_id)
    status = get_persona_agent(persona_id)
    query = " ".join(part for part in [request.question, request.book_context] if part.strip())
    hits = retrieve_persona_snippets(
        persona_id,
        PersonaRAGQueryRequest(query=query, top_k=request.top_k, categories=request.categories),
    )
    persona_context = "\n".join([f"- {hit.source_category}/{hit.snippet_type}: {hit.text}" for hit in hits])
    return PersonaPromptPreview(
        persona_id=bundle.config.persona_id,
        display_name=status.display_name,
        model_name=status.resolved_model_name,
        base_url=status.resolved_base_url,
        has_api_key=status.has_api_key,
        system_prompt=build_persona_system_prompt(persona_id, task="qa"),
        persona_context=persona_context,
        retrieved_hits=hits,
    )


def build_persona_user_prompt(
    *,
    persona_id: str,
    task: str,
    book_title: str,
    question: str,
    visible_contexts: list[str],
    current_chapter: int,
    highlight_text: str = "",
    persona_hits: list[PersonaRAGHit] | None = None,
    conversation_history: list[ChatMessage] | None = None,
) -> str:
    persona_hits = persona_hits or []
    conversation_history = conversation_history or []
    context_block = "\n\n".join([f"[书本证据 {index}]\n{text}" for index, text in enumerate(visible_contexts, start=1)])
    persona_block = "\n".join(
        [f"- {hit.source_category}/{hit.snippet_type} | {hit.title}: {hit.text}" for hit in persona_hits]
    )
    task_instruction = "请基于这些证据写一段章节总结。" if task == "summary" else "请直接回答用户问题。"
    pieces = [
        f"书名：{book_title}",
        f"当前已读章节上限：第 {current_chapter} 章",
        f"任务类型：{task}",
        task_instruction,
    ]
    if question.strip():
        pieces.append(f"用户问题：{question.strip()}")
    if highlight_text.strip():
        pieces.append(f"用户高亮：{highlight_text.strip()}")
    if conversation_history:
        history_block = "\n".join(
            [f"{'用户' if turn.role == 'user' else '助手'}：{turn.content}" for turn in conversation_history[-6:]]
        )
        pieces.extend(["已有对话记录：", history_block])
    pieces.extend(
        [
            "当前可见书本证据（来自阅读进度约束后的正文/知识图谱检索）：",
            context_block or "无",
            f"可引用的人设资料类别：{_format_categories([hit.source_category for hit in persona_hits])}",
            "Celebrity-skill 相关资料：",
            persona_block or "无",
            "输出要求：",
            "1. 必须把书本证据作为回答基础。",
            "2. 可以借 celebrity-skill 资料决定视角、语气和分析框架，但不能让资料压过当前文本。",
            "3. 如果证据不足，请明确说当前文本还不足以下结论。",
            "4. 绝对不要泄露未来剧情，不要虚构原文或名家原话。",
        ]
    )
    return "\n".join(pieces)


def generate_persona_response(
    *,
    persona_id: str,
    task: str,
    book_title: str,
    question: str,
    visible_contexts: list[str],
    current_chapter: int,
    highlight_text: str = "",
    top_k: int = 5,
    categories: list[str] | None = None,
    conversation_history: list[ChatMessage] | None = None,
) -> tuple[str, str, list[PersonaRAGHit]]:
    categories = categories or []
    ensure_persona_assets(persona_id)
    config, api_key, base_url, model_name = resolve_persona_runtime(persona_id)
    retrieval_query = " ".join(
        part for part in [book_title, question, highlight_text, "\n".join(visible_contexts)] if part.strip()
    )
    persona_hits = retrieve_persona_snippets(
        persona_id,
        PersonaRAGQueryRequest(query=retrieval_query, top_k=top_k, categories=categories),
    )
    system_prompt = build_persona_system_prompt(persona_id, task)
    user_prompt = build_persona_user_prompt(
        persona_id=persona_id,
        task=task,
        book_title=book_title,
        question=question,
        visible_contexts=visible_contexts,
        current_chapter=current_chapter,
        highlight_text=highlight_text,
        persona_hits=persona_hits,
        conversation_history=conversation_history,
    )
    try:
        messages = [{"role": "system", "content": system_prompt}]
        for turn in (conversation_history or [])[-6:]:
            messages.append({"role": turn.role, "content": turn.content})
        messages.append({"role": "user", "content": user_prompt})
        answer = invoke_openai_compatible_messages(
            api_key=api_key,
            base_url=base_url,
            model_name=model_name,
            messages=messages,
        )
    except Exception as exc:  # pragma: no cover
        raise PersonaAgentInvocationError(
            f"persona agent `{config.agent_id}` failed to generate a response: {exc}"
        ) from exc
    return answer.strip(), model_name, persona_hits
