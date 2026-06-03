from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


CATALOG_ROOT = REPO_ROOT / "data" / "raw" / "persona_sources"
PERSONA_ROOT = REPO_ROOT / "data" / "processed" / "personas"
DEFAULT_OUTPUT_ROOT = PERSONA_ROOT / "persona_kb"

CATEGORY_KEYS = ("works", "voice_sources", "biography_and_critical")


@dataclass
class PersonaAssets:
    catalog_path: Path
    persona_path: Path
    catalog: dict[str, Any]
    persona: dict[str, Any]


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _clip(text: str, limit: int = 320) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: max(0, limit - 3)].rstrip() + "..."


def _slugify(value: str) -> str:
    chars: list[str] = []
    previous_was_sep = False
    for char in value.strip().lower():
        if char.isascii() and char.isalnum():
            chars.append(char)
            previous_was_sep = False
            continue
        if previous_was_sep:
            continue
        chars.append("_")
        previous_was_sep = True
    slug = "".join(chars).strip("_")
    return slug or "item"


def _resolve_assets(persona_id: str) -> PersonaAssets:
    catalog_matches = sorted(CATALOG_ROOT.glob(f"catalog_{persona_id.removeprefix('persona_')}__*.json"))
    if not catalog_matches:
        raise FileNotFoundError(f"Missing catalog for {persona_id}")

    persona_matches = sorted(PERSONA_ROOT.glob(f"{persona_id}__*.json"))
    if not persona_matches:
        raise FileNotFoundError(f"Missing persona pack for {persona_id}")

    catalog_path = catalog_matches[-1]
    persona_path = persona_matches[-1]
    return PersonaAssets(
        catalog_path=catalog_path,
        persona_path=persona_path,
        catalog=_read_json(catalog_path),
        persona=_read_json(persona_path),
    )


def _source_document(
    *,
    persona_id: str,
    persona_name: str,
    category: str,
    rank: int,
    item: dict[str, Any],
) -> dict[str, Any]:
    source_id = str(item.get("source_id") or f"{persona_id}_{category}_{rank:03d}")
    title = str(item.get("title") or source_id)
    source_type = str(item.get("source_type") or "unknown")
    year_or_period = str(item.get("year_or_period") or "unknown")
    why_it_matters = str(item.get("why_it_matters") or "")
    source_url = str(item.get("source_url") or "")
    copyright_status = str(item.get("copyright_status") or "unknown")
    redistributable = bool(item.get("redistributable", False))

    text = (
        f"{persona_name} source in category {category}. "
        f"Title: {title}. "
        f"Type: {source_type}. "
        f"Time: {year_or_period}. "
        f"Why it matters: {why_it_matters}"
    )
    if source_url:
        text += f" Source URL: {source_url}."

    return {
        "document_id": f"{persona_id}__source__{source_id}",
        "persona_id": persona_id,
        "persona_name": persona_name,
        "document_type": "source_document",
        "source_category": category,
        "source_id": source_id,
        "title": title,
        "source_type": source_type,
        "year_or_period": year_or_period,
        "source_url": source_url,
        "copyright_status": copyright_status,
        "redistributable": redistributable,
        "text": _clip(text, 1200),
        "summary": _clip(why_it_matters, 400),
        "tags": [
            persona_id,
            category,
            source_type,
            "persona_source",
            "traditional_rag",
        ],
        "metadata": {
            "rank_in_category": rank,
            "retrieval_priority": 3 if category == "voice_sources" else 2,
        },
    }


def _persona_profile_document(persona: dict[str, Any]) -> dict[str, Any]:
    persona_id = str(persona["persona_id"])
    persona_name = str(persona["display_name"])
    fact_layer = persona.get("fact_layer", {})
    style_layer = persona.get("style_layer", {})
    stance_layer = persona.get("stance_layer", {})
    constraints = persona.get("constraints", {})

    profile_text = (
        f"{persona_name} persona profile. "
        f"Bio summary: {fact_layer.get('bio_summary', '')} "
        f"Era context: {fact_layer.get('era_context', '')} "
        f"Themes: {', '.join(fact_layer.get('themes', []))}. "
        f"Representative views: {'; '.join(fact_layer.get('representative_views', []))}. "
        f"Tone keywords: {', '.join(style_layer.get('tone_keywords', []))}. "
        f"Reasoning steps: {'; '.join(style_layer.get('reasoning_steps', []))}. "
        f"Core positions: {'; '.join(stance_layer.get('core_positions', []))}. "
        f"Knowledge boundary: {constraints.get('knowledge_boundary', '')}"
    )

    return {
        "document_id": f"{persona_id}__profile",
        "persona_id": persona_id,
        "persona_name": persona_name,
        "document_type": "persona_profile",
        "source_category": "persona_pack",
        "source_id": persona_id,
        "title": f"{persona_name} Persona Pack",
        "source_type": str(persona.get("persona_type") or "author"),
        "year_or_period": "timeless_profile",
        "source_url": "",
        "copyright_status": "project_authored_metadata",
        "redistributable": True,
        "text": _clip(profile_text, 1500),
        "summary": _clip(profile_text, 400),
        "tags": [
            persona_id,
            "persona_pack",
            "style_profile",
            "traditional_rag",
        ],
        "metadata": {
            "target_book_ids": persona.get("target_book_ids", []),
            "max_response_length": constraints.get("max_response_length"),
        },
    }


def _snippet_row(
    *,
    persona_id: str,
    persona_name: str,
    document_id: str,
    source_id: str,
    source_category: str,
    snippet_type: str,
    title: str,
    text: str,
    tags: list[str],
    weight: float,
) -> dict[str, Any]:
    snippet_slug = _slugify(f"{document_id}_{snippet_type}_{title}")[:80]
    return {
        "snippet_id": f"{persona_id}__snippet__{snippet_slug}",
        "persona_id": persona_id,
        "persona_name": persona_name,
        "document_id": document_id,
        "source_id": source_id,
        "source_category": source_category,
        "snippet_type": snippet_type,
        "title": title,
        "text": _clip(text, 900),
        "retrieval_text": _clip(f"{persona_name} {source_category} {snippet_type}. {text}", 1000),
        "tags": tags,
        "retrieval_weight": weight,
    }


def _source_snippets(document: dict[str, Any]) -> list[dict[str, Any]]:
    persona_id = str(document["persona_id"])
    persona_name = str(document["persona_name"])
    source_category = str(document["source_category"])
    source_id = str(document["source_id"])
    title = str(document["title"])
    summary = str(document.get("summary") or "")
    source_type = str(document.get("source_type") or "unknown")
    year_or_period = str(document.get("year_or_period") or "unknown")
    base_text = (
        f"{title}. Category: {source_category}. Type: {source_type}. "
        f"Time: {year_or_period}. Why it matters: {summary}"
    )
    return [
        _snippet_row(
            persona_id=persona_id,
            persona_name=persona_name,
            document_id=str(document["document_id"]),
            source_id=source_id,
            source_category=source_category,
            snippet_type="source_overview",
            title=title,
            text=base_text,
            tags=[persona_id, source_category, source_type, "source_overview"],
            weight=1.0 if source_category == "voice_sources" else 0.85,
        )
    ]


def _persona_snippets(persona: dict[str, Any], profile_document: dict[str, Any]) -> list[dict[str, Any]]:
    persona_id = str(persona["persona_id"])
    persona_name = str(persona["display_name"])
    fact_layer = persona.get("fact_layer", {})
    style_layer = persona.get("style_layer", {})
    stance_layer = persona.get("stance_layer", {})
    constraints = persona.get("constraints", {})
    document_id = str(profile_document["document_id"])

    rows = [
        _snippet_row(
            persona_id=persona_id,
            persona_name=persona_name,
            document_id=document_id,
            source_id=persona_id,
            source_category="persona_pack",
            snippet_type="bio_summary",
            title="bio_summary",
            text=fact_layer.get("bio_summary", ""),
            tags=[persona_id, "persona_pack", "bio_summary"],
            weight=0.95,
        ),
        _snippet_row(
            persona_id=persona_id,
            persona_name=persona_name,
            document_id=document_id,
            source_id=persona_id,
            source_category="persona_pack",
            snippet_type="era_context",
            title="era_context",
            text=fact_layer.get("era_context", ""),
            tags=[persona_id, "persona_pack", "era_context"],
            weight=0.85,
        ),
        _snippet_row(
            persona_id=persona_id,
            persona_name=persona_name,
            document_id=document_id,
            source_id=persona_id,
            source_category="persona_pack",
            snippet_type="style_profile",
            title="style_profile",
            text=(
                f"Tone keywords: {', '.join(style_layer.get('tone_keywords', []))}. "
                f"Reasoning steps: {'; '.join(style_layer.get('reasoning_steps', []))}. "
                f"Preferred rhetoric: {', '.join(style_layer.get('preferred_rhetoric', []))}."
            ),
            tags=[persona_id, "persona_pack", "style_profile"],
            weight=1.0,
        ),
        _snippet_row(
            persona_id=persona_id,
            persona_name=persona_name,
            document_id=document_id,
            source_id=persona_id,
            source_category="persona_pack",
            snippet_type="knowledge_boundary",
            title="knowledge_boundary",
            text=constraints.get("knowledge_boundary", ""),
            tags=[persona_id, "persona_pack", "knowledge_boundary"],
            weight=0.8,
        ),
    ]

    for theme in fact_layer.get("themes", []):
        rows.append(
            _snippet_row(
                persona_id=persona_id,
                persona_name=persona_name,
                document_id=document_id,
                source_id=persona_id,
                source_category="persona_pack",
                snippet_type="theme",
                title=theme,
                text=f"{persona_name} recurring theme: {theme}",
                tags=[persona_id, "persona_pack", "theme"],
                weight=0.7,
            )
        )

    for view in fact_layer.get("representative_views", []):
        rows.append(
            _snippet_row(
                persona_id=persona_id,
                persona_name=persona_name,
                document_id=document_id,
                source_id=persona_id,
                source_category="persona_pack",
                snippet_type="representative_view",
                title=view[:48],
                text=view,
                tags=[persona_id, "persona_pack", "representative_view"],
                weight=0.8,
            )
        )

    for position in stance_layer.get("core_positions", []):
        rows.append(
            _snippet_row(
                persona_id=persona_id,
                persona_name=persona_name,
                document_id=document_id,
                source_id=persona_id,
                source_category="persona_pack",
                snippet_type="core_position",
                title=position[:48],
                text=position,
                tags=[persona_id, "persona_pack", "core_position"],
                weight=0.9,
            )
        )

    return [row for row in rows if row["text"]]


def build_persona_kb(persona_id: str, output_root: Path) -> dict[str, Any]:
    assets = _resolve_assets(persona_id)
    persona = assets.persona
    catalog = assets.catalog
    persona_name = str(persona["display_name"])

    output_dir = output_root / persona_id
    documents: list[dict[str, Any]] = [_persona_profile_document(persona)]
    snippets: list[dict[str, Any]] = _persona_snippets(persona, documents[0])
    source_counts: dict[str, int] = {}

    for category in CATEGORY_KEYS:
        items = list(catalog.get(category, []))
        source_counts[category] = len(items)
        for rank, item in enumerate(items, start=1):
            document = _source_document(
                persona_id=persona_id,
                persona_name=persona_name,
                category=category,
                rank=rank,
                item=item,
            )
            documents.append(document)
            snippets.extend(_source_snippets(document))

    documents_path = output_dir / "documents.jsonl"
    snippets_path = output_dir / "retrieval_snippets.jsonl"
    manifest_path = output_dir / "manifest.json"

    _write_jsonl(documents_path, documents)
    _write_jsonl(snippets_path, snippets)

    manifest = {
        "kb_version": "1.0",
        "persona_id": persona_id,
        "persona_name": persona_name,
        "catalog_path": str(assets.catalog_path.relative_to(REPO_ROOT).as_posix()),
        "persona_pack_path": str(assets.persona_path.relative_to(REPO_ROOT).as_posix()),
        "output_dir": str(output_dir.relative_to(REPO_ROOT).as_posix()),
        "files": {
            "documents": str(documents_path.relative_to(REPO_ROOT).as_posix()),
            "retrieval_snippets": str(snippets_path.relative_to(REPO_ROOT).as_posix()),
        },
        "document_counts": {
            "total": len(documents),
            "persona_profile": 1,
            "source_documents": len(documents) - 1,
            **source_counts,
        },
        "snippet_counts": {
            "total": len(snippets),
            "source_overview": sum(1 for row in snippets if row["snippet_type"] == "source_overview"),
            "persona_pack": sum(1 for row in snippets if row["source_category"] == "persona_pack"),
        },
        "categories": {
            "works": {
                "description": "Books, collections, novellas, or major authored works that anchor subject matter and recurring themes.",
                "count": source_counts.get("works", 0),
            },
            "voice_sources": {
                "description": "Essays, prefaces, speeches, letters, autobiographical materials, and other sources that expose voice and reasoning style.",
                "count": source_counts.get("voice_sources", 0),
            },
            "biography_and_critical": {
                "description": "Biographical and critical references used to ground chronology, reception, and background claims.",
                "count": source_counts.get("biography_and_critical", 0),
            },
        },
        "retrieval_notes": [
            "Use persona_pack snippets for style and stance priming.",
            "Use voice_sources first when the task asks how the persona would read or comment.",
            "Use works and biography_and_critical entries as supporting evidence, not as substitutes for the current reader-visible book context.",
            "This KB stores metadata-rich summaries only; it does not contain full copyrighted text.",
        ],
    }
    _write_json(manifest_path, manifest)
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build retrieval-ready local persona KB skeletons from catalogs and persona packs."
    )
    parser.add_argument(
        "--persona-id",
        action="append",
        dest="persona_ids",
        help="Persona ID to build. Can be repeated. Defaults to all supported lead readers.",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=DEFAULT_OUTPUT_ROOT,
        help="Directory where persona_kb outputs should be written.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    persona_ids = args.persona_ids or [
        "persona_lu_xun",
        "persona_mark_twain",
        "persona_zhang_ailing",
    ]
    manifests = [build_persona_kb(persona_id, args.output_root) for persona_id in persona_ids]
    print(json.dumps({"built_personas": manifests}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
