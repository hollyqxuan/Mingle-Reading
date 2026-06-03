from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.api.schemas import BookChunk, BookRecord
from backend.knowledge_graph.builder import TemporalGraphBuilder
from backend.data_pipeline.ingest.parser import (
    extract_candidate_characters,
    normalize_text,
    score_spoiler_level,
    slugify,
    spoiler_label,
    split_chapters,
)


CHUNK_LEVELS = (
    "l0_raw_paragraph",
    "l1_fine_grained",
    "l2_structure_summary",
    "l3_global_index",
    "l4_quote_or_stance",
)

RAW_SOURCE_TYPES = {
    "public_domain_book",
    "open_license_book",
    "licensed_book",
    "project_demo_book",
}

RAW_COPYRIGHT_STATUSES = {
    "public_domain",
    "open_license",
    "licensed",
    "internal_demo_only",
    "unknown",
}

NON_ENTITY_TOKENS = {
    "Chapter",
    "You",
    "He",
    "She",
    "They",
    "We",
    "I",
    "It",
}


@dataclass
class ChapterParagraph:
    chapter_index: int
    chapter_id: str
    chapter_title: str
    paragraph_index: int
    paragraph_id: str
    text: str
    char_start: int
    char_end: int
    spoiler_level: int
    candidate_characters: list[str]
    tags: list[str]
    salience: dict[str, float]


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


def _clip_text(text: str, limit: int) -> str:
    stripped = " ".join(text.split())
    if len(stripped) <= limit:
        return stripped
    return stripped[: max(0, limit - 3)].rstrip() + "..."


def _guess_language(text: str) -> str:
    if re.search(r"[\u4e00-\u9fff]", text):
        return "zh"
    return "en"


def _coerce_raw_record(payload: dict[str, Any], path: Path) -> dict[str, Any]:
    content = str(payload.get("content") or "")
    title = str(payload.get("title") or path.stem.replace("_", " ").title())
    book_id = str(payload.get("book_id") or f"book_{slugify(title)}")
    source_format = str(payload.get("source_format") or path.suffix.lstrip(".") or "json")
    source_type = str(payload.get("source_type") or "project_demo_book")
    if source_type not in RAW_SOURCE_TYPES:
        source_type = "project_demo_book"
    copyright_status = str(payload.get("copyright_status") or "unknown")
    if copyright_status not in RAW_COPYRIGHT_STATUSES:
        copyright_status = "unknown"
    normalized = normalize_text(content)
    chapters = split_chapters(normalized)
    return {
        "record_id": str(payload.get("record_id") or f"raw_{book_id}_v001"),
        "book_id": book_id,
        "title": title,
        "author": str(payload.get("author") or ""),
        "language": str(payload.get("language") or _guess_language(normalized)),
        "source_type": source_type,
        "source_format": source_format or "json",
        "copyright_status": copyright_status,
        "source_uri": str(payload.get("source_uri") or path.as_posix()),
        "license_note": str(payload.get("license_note") or ""),
        "ingest_date": str(payload.get("ingest_date") or date.today().isoformat()),
        "chapter_titles": payload.get("chapter_titles") or [chapter_title for chapter_title, _ in chapters],
        "content": normalized,
        "notes": str(payload.get("notes") or ""),
    }


def _raw_record_from_txt(path: Path, title: str | None) -> dict[str, Any]:
    content = normalize_text(path.read_text(encoding="utf-8"))
    resolved_title = title or path.stem.replace("_", " ").title()
    book_id = f"book_{slugify(resolved_title)}"
    chapters = split_chapters(content)
    return {
        "record_id": f"raw_{book_id}_v001",
        "book_id": book_id,
        "title": resolved_title,
        "author": "",
        "language": _guess_language(content),
        "source_type": "project_demo_book",
        "source_format": "txt",
        "copyright_status": "unknown",
        "source_uri": path.as_posix(),
        "license_note": "",
        "ingest_date": date.today().isoformat(),
        "chapter_titles": [chapter_title for chapter_title, _ in chapters],
        "content": content,
        "notes": "Generated from plain text input by backend/scripts/hierarchical_dataset_builder.py",
    }


def _book_record_from_json(path: Path) -> BookRecord | None:
    payload = _read_json(path)
    required = {"book_id", "title", "source_path", "chapter_count", "chunks"}
    if not required.issubset(payload.keys()):
        return None
    return BookRecord.model_validate(payload)


def _load_input(path: Path, title: str | None) -> tuple[dict[str, Any], BookRecord | None]:
    if path.suffix.lower() == ".txt":
        return _raw_record_from_txt(path, title), None
    payload = _read_json(path)
    book_record = _book_record_from_json(path)
    if book_record is not None:
        raw_record = {
            "record_id": f"raw_{book_record.book_id}_v001",
            "book_id": book_record.book_id,
            "title": book_record.title,
            "author": "",
            "language": "unknown",
            "source_type": "project_demo_book",
            "source_format": "json",
            "copyright_status": "unknown",
            "source_uri": book_record.source_path,
            "license_note": "",
            "ingest_date": date.today().isoformat(),
            "chapter_titles": sorted(
                {chunk.metadata.get("chapter_title", chunk.chapter_id) for chunk in book_record.chunks}
            ),
            "content": "\n\n".join(chunk.text for chunk in book_record.chunks if chunk.chunk_level == "l0_raw_paragraph"),
            "notes": "Derived from serialized BookRecord input.",
        }
        return raw_record, book_record
    return _coerce_raw_record(payload, path), None


def _compute_salience(text: str, chapter_index: int) -> dict[str, float]:
    sentence_count = max(1, len(re.findall(r"[.!?銆傦紒锛燂紱;]", text)))
    exclamations = len(re.findall(r"[!?锛侊紵]", text))
    quotes = len(re.findall(r"[\"鈥溾€?鈥樷€欍€屻€嶃€庛€廬", text))
    conflict_markers = len(
        re.findall(
            r"(fight|war|anger|fear|death|secret|against|refuse|conflict|argue|betray|鍝瓅鎬抾鎭▅鎬晐姝粅绉樺瘑|浜墊鍚祙鎷?",
            text,
            flags=re.IGNORECASE,
        )
    )
    abstract_markers = len(
        re.findall(
            r"(memory|freedom|truth|fate|history|dream|identity|meaning|symbol|鍛借繍|璁板繂|鑷敱|鐪熺浉|鍘嗗彶|鐞嗘兂|璞″緛)",
            text,
            flags=re.IGNORECASE,
        )
    )
    emotion = min(1.0, round((len(text) / 320 + exclamations * 0.18 + quotes * 0.05) / 2.0, 3))
    conflict = min(1.0, round((conflict_markers * 0.28 + exclamations * 0.15) / max(1, sentence_count), 3))
    psychological = min(1.0, round((abstract_markers * 0.22 + len(text) / 520) / 1.5, 3))
    symbolic = min(1.0, round((abstract_markers * 0.25 + quotes * 0.08 + chapter_index * 0.04) / 1.4, 3))
    return {
        "emotion_intensity": emotion,
        "conflict_intensity": conflict,
        "psychological_complexity": psychological,
        "symbolic_density": symbolic,
    }


def _paragraph_tags(text: str) -> list[str]:
    tags: list[str] = ["book_text"]
    lowered = text.lower()
    if re.search(r"[\"鈥溾€?鈥樷€欍€屻€嶃€庛€廬", text):
        tags.append("dialogue")
    if re.search(r"(memory|remember|past|鍥炲繂|璁板緱|寰€浜?", lowered):
        tags.append("memory")
    if re.search(r"(dream|future|鍛借繍|鐞嗘兂|鏈潵)", lowered):
        tags.append("foreshadowing")
    if re.search(r"(fight|argue|war|anger|浜墊鍚祙鎬?", lowered):
        tags.append("conflict")
    if len(tags) == 1:
        tags.append("narrative_context")
    return tags


def _paragraph_rows_from_raw(raw_record: dict[str, Any]) -> list[ChapterParagraph]:
    rows: list[ChapterParagraph] = []
    char_cursor = 0
    chapter_id_seen = 0
    for chapter_index, (chapter_title, chapter_body) in enumerate(split_chapters(raw_record["content"]), start=1):
        chapter_id_seen += 1
        chapter_id = f"ch_{chapter_id_seen:03d}"
        chapter_body = re.sub(rf"^\s*{re.escape(chapter_title)}\s*", "", chapter_body, count=1).strip()
        paragraphs = [part.strip() for part in chapter_body.split("\n\n") if part.strip()]
        for paragraph_index, paragraph in enumerate(paragraphs, start=1):
            char_start = char_cursor
            char_end = char_start + len(paragraph)
            char_cursor = char_end + 2
            rows.append(
                ChapterParagraph(
                    chapter_index=chapter_index,
                    chapter_id=chapter_id,
                    chapter_title=chapter_title,
                    paragraph_index=paragraph_index,
                    paragraph_id=f"para_{paragraph_index:04d}",
                    text=paragraph,
                    char_start=char_start,
                    char_end=char_end,
                    spoiler_level=score_spoiler_level(chapter_index),
                    candidate_characters=_normalize_candidate_characters(extract_candidate_characters(paragraph)),
                    tags=_paragraph_tags(paragraph),
                    salience=_compute_salience(paragraph, chapter_index),
                )
            )
    return rows


def _paragraph_rows_from_book(book: BookRecord) -> list[ChapterParagraph]:
    rows: list[ChapterParagraph] = []
    for chunk in sorted(book.chunks, key=lambda item: (item.chapter_index, item.paragraph_index, item.chunk_id)):
        if chunk.chunk_level != "l0_raw_paragraph":
            continue
        rows.append(
            ChapterParagraph(
                chapter_index=chunk.chapter_index,
                chapter_id=chunk.chapter_id,
                chapter_title=str(chunk.metadata.get("chapter_title", chunk.chapter_id)),
                paragraph_index=chunk.paragraph_index,
                paragraph_id=chunk.paragraph_id.replace("paragraph-", "para_") if "paragraph-" in chunk.paragraph_id else chunk.paragraph_id,
                text=chunk.text,
                char_start=chunk.position.get("char_start", 0),
                char_end=chunk.position.get("char_end", len(chunk.text)),
                spoiler_level=chunk.spoiler_level,
                candidate_characters=_normalize_candidate_characters(chunk.candidate_characters),
                tags=chunk.tags or _paragraph_tags(chunk.text),
                salience=_compute_salience(chunk.text, chunk.chapter_index),
            )
        )
    return rows


def _normalize_candidate_characters(names: list[str]) -> list[str]:
    deduped: list[str] = []
    for name in names:
        cleaned = name.strip()
        if not cleaned or cleaned in NON_ENTITY_TOKENS:
            continue
        if cleaned not in deduped:
            deduped.append(cleaned)
    return deduped


def _chunk_record(
    *,
    chunk_id: str,
    book_id: str,
    chapter_id: str,
    section_id: str | None,
    paragraph_start_id: str | None,
    paragraph_end_id: str | None,
    chunk_level: str,
    text: str,
    chapter_index: int,
    section_index: int,
    char_start: int,
    char_end: int,
    characters_present: list[str],
    tags: list[str],
    salience: dict[str, float],
    source_record_id: str,
) -> dict[str, Any]:
    spoiler = spoiler_label(score_spoiler_level(chapter_index))
    return {
        "chunk_id": chunk_id,
        "book_id": book_id,
        "chapter_id": chapter_id,
        "section_id": section_id,
        "paragraph_start_id": paragraph_start_id,
        "paragraph_end_id": paragraph_end_id,
        "chunk_level": chunk_level,
        "text": text,
        "position": {
            "chapter_index": chapter_index,
            "section_index": section_index,
            "char_start": char_start,
            "char_end": char_end,
        },
        "characters_present": sorted(set(characters_present)),
        "tags": sorted(set(tags)),
        "salience": salience,
        "spoiler_guard": {
            "spoiler_level": spoiler,
            "max_visible_chapter_index": chapter_index,
        },
        "source_record_id": source_record_id,
    }


def _build_l0(book_id: str, source_record_id: str, paragraphs: list[ChapterParagraph]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for paragraph in paragraphs:
        rows.append(
            _chunk_record(
                chunk_id=f"chunk_{book_id.replace('book_', '')}_{paragraph.chapter_index:03d}_{paragraph.paragraph_index:04d}",
                book_id=book_id,
                chapter_id=paragraph.chapter_id,
                section_id=f"sec_{paragraph.chapter_index:03d}",
                paragraph_start_id=paragraph.paragraph_id,
                paragraph_end_id=paragraph.paragraph_id,
                chunk_level="l0_raw_paragraph",
                text=paragraph.text,
                chapter_index=paragraph.chapter_index,
                section_index=paragraph.chapter_index,
                char_start=paragraph.char_start,
                char_end=paragraph.char_end,
                characters_present=paragraph.candidate_characters,
                tags=paragraph.tags,
                salience=paragraph.salience,
                source_record_id=source_record_id,
            )
        )
    return rows


def _build_l1(book_id: str, source_record_id: str, paragraphs: list[ChapterParagraph], window_size: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    grouped: dict[int, list[ChapterParagraph]] = defaultdict(list)
    for paragraph in paragraphs:
        grouped[paragraph.chapter_index].append(paragraph)
    for chapter_index, items in grouped.items():
        chapter_id = items[0].chapter_id
        for start in range(0, len(items), window_size):
            window = items[start : start + window_size]
            if not window:
                continue
            tags = [tag for item in window for tag in item.tags] + ["retrieval_window"]
            salience = {
                key: round(sum(item.salience[key] for item in window) / len(window), 3)
                for key in window[0].salience
            }
            rows.append(
                _chunk_record(
                    chunk_id=f"chunk_{book_id.replace('book_', '')}_{chapter_index:03d}_fg_{start // window_size + 1:04d}",
                    book_id=book_id,
                    chapter_id=chapter_id,
                    section_id=f"sec_{chapter_index:03d}",
                    paragraph_start_id=window[0].paragraph_id,
                    paragraph_end_id=window[-1].paragraph_id,
                    chunk_level="l1_fine_grained",
                    text="\n\n".join(item.text for item in window),
                    chapter_index=chapter_index,
                    section_index=chapter_index,
                    char_start=window[0].char_start,
                    char_end=window[-1].char_end,
                    characters_present=[name for item in window for name in item.candidate_characters],
                    tags=tags,
                    salience=salience,
                    source_record_id=source_record_id,
                )
            )
    return rows


def _build_l2(book_id: str, source_record_id: str, paragraphs: list[ChapterParagraph]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    grouped: dict[int, list[ChapterParagraph]] = defaultdict(list)
    for paragraph in paragraphs:
        grouped[paragraph.chapter_index].append(paragraph)
    for chapter_index, items in grouped.items():
        lead = _clip_text(items[0].text, 120)
        tail = _clip_text(items[-1].text, 120)
        character_names = sorted({name for item in items for name in item.candidate_characters})
        summary = (
            f"{items[0].chapter_title}: opens with {lead}. "
            f"It develops across {len(items)} paragraph(s) and closes with {tail}. "
            f"Visible character/entity mentions: {', '.join(character_names) if character_names else 'none yet'}."
        )
        salience = {
            key: round(sum(item.salience[key] for item in items) / len(items), 3)
            for key in items[0].salience
        }
        rows.append(
            _chunk_record(
                chunk_id=f"chunk_{book_id.replace('book_', '')}_{chapter_index:03d}_summary_0001",
                book_id=book_id,
                chapter_id=items[0].chapter_id,
                section_id=f"sec_{chapter_index:03d}",
                paragraph_start_id=items[0].paragraph_id,
                paragraph_end_id=items[-1].paragraph_id,
                chunk_level="l2_structure_summary",
                text=summary,
                chapter_index=chapter_index,
                section_index=chapter_index,
                char_start=items[0].char_start,
                char_end=items[-1].char_end,
                characters_present=character_names,
                tags=["chapter_summary", "structure"],
                salience=salience,
                source_record_id=source_record_id,
            )
        )
    return rows


def _build_l3(book_id: str, source_record_id: str, paragraphs: list[ChapterParagraph]) -> list[dict[str, Any]]:
    chapter_count = len({paragraph.chapter_index for paragraph in paragraphs})
    chapter_titles = []
    character_names = sorted({name for paragraph in paragraphs for name in paragraph.candidate_characters})
    for chapter_index in range(1, chapter_count + 1):
        chapter_rows = [paragraph for paragraph in paragraphs if paragraph.chapter_index == chapter_index]
        if not chapter_rows:
            continue
        chapter_titles.append(f"{chapter_rows[0].chapter_id}:{chapter_rows[0].chapter_title}")
    global_text = (
        f"Global index for {book_id}. "
        f"Chapters={chapter_count}. "
        f"Chapter map: {' | '.join(chapter_titles)}. "
        f"Known visible entities: {', '.join(character_names) if character_names else 'none detected'}. "
        f"Designed for progress-aware retrieval and routing."
    )
    return [
        _chunk_record(
            chunk_id=f"chunk_{book_id.replace('book_', '')}_global_0001",
            book_id=book_id,
            chapter_id="ch_000",
            section_id="sec_000",
            paragraph_start_id=None,
            paragraph_end_id=None,
            chunk_level="l3_global_index",
            text=global_text,
            chapter_index=0,
            section_index=0,
            char_start=0,
            char_end=max(1, len(global_text)),
            characters_present=character_names,
            tags=["global_index", "routing"],
            salience={
                "emotion_intensity": 0.1,
                "conflict_intensity": 0.05,
                "psychological_complexity": 0.3,
                "symbolic_density": 0.2,
            },
            source_record_id=source_record_id,
        )
    ]


def _build_l4(book_id: str, source_record_id: str, paragraphs: list[ChapterParagraph], max_quotes_per_chapter: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    grouped: dict[int, list[ChapterParagraph]] = defaultdict(list)
    for paragraph in paragraphs:
        grouped[paragraph.chapter_index].append(paragraph)
    for chapter_index, items in grouped.items():
        ranked = sorted(
            items,
            key=lambda item: (
                "dialogue" in item.tags,
                item.salience["emotion_intensity"] + item.salience["psychological_complexity"],
                len(item.text),
            ),
            reverse=True,
        )
        for local_index, item in enumerate(ranked[:max_quotes_per_chapter], start=1):
            stance = (
                f"Quote/stance placeholder for chapter {chapter_index}: "
                f"anchor quote='{_clip_text(item.text, 90)}'. "
                f"Reader-facing stance: explain the subtext, emotional pressure, and spoiler-safe interpretive angle."
            )
            rows.append(
                _chunk_record(
                    chunk_id=f"chunk_{book_id.replace('book_', '')}_{chapter_index:03d}_quote_{local_index:04d}",
                    book_id=book_id,
                    chapter_id=item.chapter_id,
                    section_id=f"sec_{chapter_index:03d}",
                    paragraph_start_id=item.paragraph_id,
                    paragraph_end_id=item.paragraph_id,
                    chunk_level="l4_quote_or_stance",
                    text=stance,
                    chapter_index=chapter_index,
                    section_index=chapter_index,
                    char_start=item.char_start,
                    char_end=item.char_end,
                    characters_present=item.candidate_characters,
                    tags=item.tags + ["quote_placeholder", "stance"],
                    salience=item.salience,
                    source_record_id=source_record_id,
                )
            )
    return rows


def _book_record_from_l0(raw_record: dict[str, Any], l0_rows: list[dict[str, Any]]) -> BookRecord:
    chunks: list[BookChunk] = []
    for row in l0_rows:
        chunks.append(
            BookChunk(
                chunk_id=row["chunk_id"],
                book_id=row["book_id"],
                chapter_id=row["chapter_id"],
                section_id=row["section_id"],
                paragraph_start_id=row["paragraph_start_id"],
                paragraph_end_id=row["paragraph_end_id"],
                chunk_level="l0_raw_paragraph",
                chapter_index=row["position"]["chapter_index"],
                paragraph_id=row["paragraph_start_id"] or "para_0000",
                paragraph_index=int(str(row["paragraph_start_id"] or "0").split("_")[-1]),
                text=row["text"],
                token_offset=row["position"]["char_start"],
                spoiler_level=row["position"]["chapter_index"],
                position=row["position"],
                spoiler_guard=row["spoiler_guard"],
                tags=row["tags"],
                candidate_characters=row["characters_present"],
                metadata={
                    "chapter_title": next(
                        (
                            chapter_title
                            for chapter_title in raw_record.get("chapter_titles", [])
                            if chapter_title
                        ),
                        row["chapter_id"],
                    )
                },
            )
        )
    chapter_title_map = {
        index + 1: title
        for index, title in enumerate(raw_record.get("chapter_titles") or [])
    }
    for chunk in chunks:
        chunk.metadata["chapter_title"] = chapter_title_map.get(chunk.chapter_index, chunk.chapter_id)
        chunk.spoiler_level = score_spoiler_level(chunk.chapter_index)
    return BookRecord(
        book_id=raw_record["book_id"],
        title=raw_record["title"],
        source_path=raw_record["source_uri"],
        chapter_count=len(chapter_title_map) or len({chunk.chapter_index for chunk in chunks}),
        chunks=chunks,
    )


def _graph_rows(graph: Any) -> dict[str, list[dict[str, Any]]]:
    episodes = list(graph.episodes.values()) if isinstance(graph.episodes, dict) else list(graph.episodes)
    entities = list(graph.entities.values()) if isinstance(graph.entities, dict) else list(graph.entities)
    relations = list(graph.relations.values()) if isinstance(graph.relations, dict) else list(graph.relations)
    communities = list(graph.communities.values()) if isinstance(graph.communities, dict) else list(graph.communities)
    sagas = list(graph.sagas.values()) if isinstance(graph.sagas, dict) else list(graph.sagas)
    return {
        "episodes": [item.model_dump(mode="json") for item in episodes],
        "entities": [item.model_dump(mode="json") for item in entities],
        "relations": [item.model_dump(mode="json") for item in relations],
        "communities": [item.model_dump(mode="json") for item in communities],
        "sagas": [item.model_dump(mode="json") for item in sagas],
    }


def build_hierarchical_dataset(
    *,
    input_path: Path,
    output_dir: Path,
    title: str | None,
    retrieval_window: int,
    quotes_per_chapter: int,
) -> dict[str, Any]:
    raw_record, maybe_book = _load_input(input_path, title)
    paragraphs = _paragraph_rows_from_book(maybe_book) if maybe_book is not None else _paragraph_rows_from_raw(raw_record)
    if not paragraphs:
        raise ValueError(f"No paragraph content extracted from {input_path}")

    source_record_id = raw_record["record_id"]
    book_id = raw_record["book_id"]
    level_rows = {
        "l0_raw_paragraph": _build_l0(book_id, source_record_id, paragraphs),
        "l1_fine_grained": _build_l1(book_id, source_record_id, paragraphs, retrieval_window),
        "l2_structure_summary": _build_l2(book_id, source_record_id, paragraphs),
        "l3_global_index": _build_l3(book_id, source_record_id, paragraphs),
        "l4_quote_or_stance": _build_l4(book_id, source_record_id, paragraphs, quotes_per_chapter),
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    _write_json(output_dir / "raw_record.json", raw_record)

    merged_rows: list[dict[str, Any]] = []
    level_files: dict[str, str] = {}
    for level_name in CHUNK_LEVELS:
        rows = level_rows[level_name]
        merged_rows.extend(rows)
        level_path = output_dir / f"{level_name}.jsonl"
        _write_jsonl(level_path, rows)
        level_files[level_name] = str(level_path)
    _write_jsonl(output_dir / "hierarchical_chunks.jsonl", merged_rows)

    graph_book = maybe_book if maybe_book is not None else _book_record_from_l0(raw_record, level_rows["l0_raw_paragraph"])
    graph = TemporalGraphBuilder().build(graph_book)
    graph_rows = _graph_rows(graph)
    graph_dir = output_dir / "graph"
    _write_json(graph_dir / "graph.json", graph.model_dump(mode="json"))
    _write_jsonl(graph_dir / "episodes.jsonl", graph_rows["episodes"])
    _write_jsonl(graph_dir / "entities.jsonl", graph_rows["entities"])
    _write_jsonl(graph_dir / "relations.jsonl", graph_rows["relations"])
    _write_jsonl(graph_dir / "communities.jsonl", graph_rows["communities"])
    _write_jsonl(graph_dir / "sagas.jsonl", graph_rows["sagas"])

    manifest = {
        "input_path": str(input_path),
        "output_dir": str(output_dir),
        "book_id": book_id,
        "title": raw_record["title"],
        "chapter_count": len({paragraph.chapter_index for paragraph in paragraphs}),
        "paragraph_count": len(paragraphs),
        "levels": {level: len(rows) for level, rows in level_rows.items()},
        "graph_counts": {
            "episodes": len(graph_rows["episodes"]),
            "entities": len(graph_rows["entities"]),
            "relations": len(graph_rows["relations"]),
            "communities": len(graph_rows["communities"]),
            "sagas": len(graph_rows["sagas"]),
        },
        "files": {
            "raw_record": str(output_dir / "raw_record.json"),
            "hierarchical_chunks": str(output_dir / "hierarchical_chunks.jsonl"),
            **level_files,
            "graph": str(graph_dir / "graph.json"),
            "episodes": str(graph_dir / "episodes.jsonl"),
            "entities": str(graph_dir / "entities.jsonl"),
            "relations": str(graph_dir / "relations.jsonl"),
            "communities": str(graph_dir / "communities.jsonl"),
            "sagas": str(graph_dir / "sagas.jsonl"),
        },
    }
    _write_json(output_dir / "manifest.json", manifest)
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build Mingle Reading hierarchical dataset artifacts.")
    parser.add_argument("input", type=Path, help="Input .txt, raw text json, or serialized BookRecord json.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("scripts") / "build_output",
        help="Directory for generated JSON/JSONL artifacts.",
    )
    parser.add_argument("--title", type=str, default=None, help="Optional override title for plain-text inputs.")
    parser.add_argument(
        "--retrieval-window",
        type=int,
        default=2,
        help="Paragraph window size used to build L1 fine-grained retrieval chunks.",
    )
    parser.add_argument(
        "--quotes-per-chapter",
        type=int,
        default=2,
        help="Number of L4 quote/stance placeholders emitted per chapter.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    manifest = build_hierarchical_dataset(
        input_path=args.input,
        output_dir=args.output_dir,
        title=args.title,
        retrieval_window=max(1, args.retrieval_window),
        quotes_per_chapter=max(1, args.quotes_per_chapter),
    )
    print(json.dumps(manifest, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()

