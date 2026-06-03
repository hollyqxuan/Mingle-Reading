from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import hierarchical_dataset_builder as hdb


BOOK_SOURCE_TYPES = {
    "public_domain_book",
    "open_license_book",
    "licensed_book",
    "project_demo_book",
}

PERSONA_SOURCE_TYPES = {
    "author_work",
    "character_source",
    "biography_reference",
    "critical_reference",
}

COPYRIGHT_STATUSES = {
    "public_domain",
    "open_license",
    "licensed",
    "internal_demo_only",
    "unknown",
}


@dataclass
class RegistrationResult:
    source_id: str
    title: str
    raw_record_path: Path
    manifest_path: Path
    processed_manifest_path: Path | None
    registry_entry: dict[str, Any]


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _canonical_slug(value: str) -> str:
    lowered = value.strip().lower()
    slug_chars: list[str] = []
    previous_was_separator = False
    for char in lowered:
        if char.isascii() and char.isalnum():
            slug_chars.append(char)
            previous_was_separator = False
            continue
        if previous_was_separator:
            continue
        slug_chars.append("_")
        previous_was_separator = True
    slug = "".join(slug_chars).strip("_")
    return slug or "untitled"


def _prefixed_id(prefix: str, value: str) -> str:
    slug = _canonical_slug(value)
    expected = f"{prefix}_"
    if slug.startswith(expected):
        return slug
    return f"{prefix}_{slug}"


def _resolve_inputs(inputs: list[Path], recursive: bool) -> list[Path]:
    resolved: list[Path] = []
    for input_path in inputs:
        if input_path.is_dir():
            pattern = "**/*" if recursive else "*"
            for candidate in sorted(input_path.glob(pattern)):
                if candidate.is_file() and candidate.suffix.lower() in {".txt", ".json"}:
                    resolved.append(candidate)
            continue
        resolved.append(input_path)
    return resolved


def _safe_value(cli_value: str | None, payload_value: Any, default: str) -> str:
    if cli_value is not None:
        return cli_value
    if payload_value is not None and str(payload_value).strip():
        return str(payload_value).strip()
    return default


def _strip_bom(value: str) -> str:
    return value.lstrip("\ufeff")


def _chapter_count(raw_record: dict[str, Any]) -> int:
    chapter_titles = raw_record.get("chapter_titles") or []
    if chapter_titles:
        return len(chapter_titles)
    content = str(raw_record.get("content") or "")
    return len(hdb.split_chapters(content))


def _normalize_book_raw_record(
    input_path: Path,
    *,
    title: str | None,
    source_type: str | None,
    copyright_status: str | None,
    author: str | None,
    language: str | None,
    license_note: str | None,
    notes: str | None,
    version: str,
) -> dict[str, Any]:
    raw_record, _ = hdb._load_input(input_path, title)
    canonical_book_id = _prefixed_id("book", str(raw_record.get("book_id") or title or input_path.stem))
    normalized_content = _strip_bom(hdb.normalize_text(str(raw_record.get("content") or "")))
    normalized_source_type = _safe_value(source_type, raw_record.get("source_type"), "project_demo_book")
    if normalized_source_type not in BOOK_SOURCE_TYPES:
        raise ValueError(f"Unsupported books source_type: {normalized_source_type}")
    normalized_copyright = _safe_value(copyright_status, raw_record.get("copyright_status"), "unknown")
    if normalized_copyright not in COPYRIGHT_STATUSES:
        raise ValueError(f"Unsupported copyright_status: {normalized_copyright}")
    chapter_titles = raw_record.get("chapter_titles") or [chapter for chapter, _ in hdb.split_chapters(normalized_content)]
    return {
        "record_id": f"raw_{canonical_book_id}_{version}",
        "book_id": canonical_book_id,
        "title": _safe_value(title, raw_record.get("title"), input_path.stem.replace("_", " ").title()),
        "author": _safe_value(author, raw_record.get("author"), ""),
        "language": _safe_value(language, raw_record.get("language"), hdb._guess_language(normalized_content)),
        "source_type": normalized_source_type,
        "source_format": str(raw_record.get("source_format") or input_path.suffix.lstrip(".") or "txt"),
        "copyright_status": normalized_copyright,
        "source_uri": str(input_path.as_posix()),
        "license_note": _safe_value(license_note, raw_record.get("license_note"), ""),
        "ingest_date": str(raw_record.get("ingest_date") or date.today().isoformat()),
        "chapter_titles": chapter_titles,
        "content": normalized_content,
        "notes": _safe_value(
            notes,
            raw_record.get("notes"),
            "Registered by backend/scripts/source_registry_manifest_builder.py",
        ),
    }


def _normalize_persona_raw_record(
    input_path: Path,
    *,
    title: str | None,
    source_type: str | None,
    copyright_status: str | None,
    author: str | None,
    language: str | None,
    license_note: str | None,
    notes: str | None,
    persona_name: str | None,
    version: str,
) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if input_path.suffix.lower() == ".json":
        payload = hdb._read_json(input_path)
        content = _strip_bom(hdb.normalize_text(str(payload.get("content") or payload.get("text") or "")))
    else:
        content = _strip_bom(hdb.normalize_text(input_path.read_text(encoding="utf-8")))

    resolved_title = _safe_value(
        title,
        payload.get("title") or payload.get("source_title"),
        input_path.stem.replace("_", " ").title(),
    )
    source_id = _prefixed_id("persona_source", payload.get("source_id") or resolved_title)
    normalized_source_type = _safe_value(source_type, payload.get("source_type"), "biography_reference")
    if normalized_source_type not in PERSONA_SOURCE_TYPES:
        raise ValueError(f"Unsupported persona_sources source_type: {normalized_source_type}")
    normalized_copyright = _safe_value(copyright_status, payload.get("copyright_status"), "unknown")
    if normalized_copyright not in COPYRIGHT_STATUSES:
        raise ValueError(f"Unsupported copyright_status: {normalized_copyright}")

    persona_value = _safe_value(persona_name, payload.get("persona_name") or payload.get("subject"), "")
    persona_id = _prefixed_id("persona", persona_value) if persona_value else ""
    return {
        "record_id": f"raw_{source_id}_{version}",
        "source_id": source_id,
        "source_kind": "persona_sources",
        "persona_id": persona_id,
        "persona_name": persona_value,
        "title": resolved_title,
        "author": _safe_value(author, payload.get("author"), ""),
        "language": _safe_value(language, payload.get("language"), hdb._guess_language(content)),
        "source_type": normalized_source_type,
        "source_format": str(payload.get("source_format") or input_path.suffix.lstrip(".") or "txt"),
        "copyright_status": normalized_copyright,
        "source_uri": str(input_path.as_posix()),
        "license_note": _safe_value(license_note, payload.get("license_note"), ""),
        "ingest_date": str(payload.get("ingest_date") or date.today().isoformat()),
        "content": content,
        "notes": _safe_value(
            notes,
            payload.get("notes"),
            "Registered by backend/scripts/source_registry_manifest_builder.py",
        ),
    }


def _stats_for_raw_record(mode: str, raw_record: dict[str, Any]) -> dict[str, Any]:
    content = str(raw_record.get("content") or "")
    stats = {
        "character_count": len(content),
        "line_count": len(content.splitlines()),
    }
    if mode == "books":
        stats["chapter_count"] = _chapter_count(raw_record)
    return stats


def _build_source_manifest(
    *,
    mode: str,
    version: str,
    raw_record: dict[str, Any],
    input_path: Path,
    raw_record_path: Path,
    processed_manifest_path: Path | None,
) -> dict[str, Any]:
    source_id = raw_record["book_id"] if mode == "books" else raw_record["source_id"]
    registry_entry_id = f"registry_{source_id}_{version}"
    manifest = {
        "manifest_version": "1.0",
        "registry_entry_id": registry_entry_id,
        "source_kind": mode,
        "source_id": source_id,
        "title": raw_record["title"],
        "source_type": raw_record["source_type"],
        "source_format": raw_record["source_format"],
        "language": raw_record["language"],
        "copyright_status": raw_record["copyright_status"],
        "ingest_date": raw_record["ingest_date"],
        "provenance": {
            "local_input_path": str(input_path.as_posix()),
            "source_uri": raw_record["source_uri"],
            "license_note": raw_record.get("license_note", ""),
        },
        "stats": _stats_for_raw_record(mode, raw_record),
        "files": {
            "raw_record": str(raw_record_path.as_posix()),
            "processed_manifest": str(processed_manifest_path.as_posix()) if processed_manifest_path else None,
        },
    }
    if mode == "books":
        manifest["book_id"] = raw_record["book_id"]
    else:
        manifest["persona_id"] = raw_record.get("persona_id", "")
        manifest["persona_name"] = raw_record.get("persona_name", "")
    return manifest


def _register_book_source(
    input_path: Path,
    *,
    output_root: Path,
    version: str,
    title: str | None,
    source_type: str | None,
    copyright_status: str | None,
    author: str | None,
    language: str | None,
    license_note: str | None,
    notes: str | None,
    build_hierarchical: bool,
    retrieval_window: int,
    quotes_per_chapter: int,
) -> RegistrationResult:
    raw_record = _normalize_book_raw_record(
        input_path,
        title=title,
        source_type=source_type,
        copyright_status=copyright_status,
        author=author,
        language=language,
        license_note=license_note,
        notes=notes,
        version=version,
    )
    book_id = raw_record["book_id"]
    raw_dir = output_root / "data" / "raw" / "books"
    processed_dir = output_root / "data" / "processed" / "books" / book_id / version
    manifests_dir = output_root / "data" / "manifests"
    raw_record_path = raw_dir / f"{book_id}__source_{raw_record['source_type']}__{version}.json"
    source_manifest_path = manifests_dir / f"manifest__books__{book_id}__{version}.json"

    _write_json(raw_record_path, raw_record)

    processed_manifest_path: Path | None = None
    if build_hierarchical:
        hdb.build_hierarchical_dataset(
            input_path=raw_record_path,
            output_dir=processed_dir,
            title=None,
            retrieval_window=max(1, retrieval_window),
            quotes_per_chapter=max(1, quotes_per_chapter),
        )
        processed_manifest_path = processed_dir / "manifest.json"

    source_manifest = _build_source_manifest(
        mode="books",
        version=version,
        raw_record=raw_record,
        input_path=input_path,
        raw_record_path=raw_record_path,
        processed_manifest_path=processed_manifest_path,
    )
    source_manifest["files"]["source_manifest"] = str(source_manifest_path.as_posix())
    _write_json(source_manifest_path, source_manifest)

    return RegistrationResult(
        source_id=book_id,
        title=raw_record["title"],
        raw_record_path=raw_record_path,
        manifest_path=source_manifest_path,
        processed_manifest_path=processed_manifest_path,
        registry_entry=source_manifest,
    )


def _register_persona_source(
    input_path: Path,
    *,
    output_root: Path,
    version: str,
    title: str | None,
    source_type: str | None,
    copyright_status: str | None,
    author: str | None,
    language: str | None,
    license_note: str | None,
    notes: str | None,
    persona_name: str | None,
) -> RegistrationResult:
    raw_record = _normalize_persona_raw_record(
        input_path,
        title=title,
        source_type=source_type,
        copyright_status=copyright_status,
        author=author,
        language=language,
        license_note=license_note,
        notes=notes,
        persona_name=persona_name,
        version=version,
    )
    source_id = raw_record["source_id"]
    raw_dir = output_root / "data" / "raw" / "persona_sources"
    manifests_dir = output_root / "data" / "manifests"
    raw_record_path = raw_dir / f"{source_id}__source_{raw_record['source_type']}__{version}.json"
    source_manifest_path = manifests_dir / f"manifest__persona_sources__{source_id}__{version}.json"

    _write_json(raw_record_path, raw_record)
    source_manifest = _build_source_manifest(
        mode="persona_sources",
        version=version,
        raw_record=raw_record,
        input_path=input_path,
        raw_record_path=raw_record_path,
        processed_manifest_path=None,
    )
    source_manifest["files"]["source_manifest"] = str(source_manifest_path.as_posix())
    _write_json(source_manifest_path, source_manifest)

    return RegistrationResult(
        source_id=source_id,
        title=raw_record["title"],
        raw_record_path=raw_record_path,
        manifest_path=source_manifest_path,
        processed_manifest_path=None,
        registry_entry=source_manifest,
    )


def _write_registry_batch(
    *,
    output_root: Path,
    mode: str,
    version: str,
    results: list[RegistrationResult],
) -> Path:
    manifests_dir = output_root / "data" / "manifests"
    registry_path = manifests_dir / f"source_registry__{mode}__{version}.json"
    payload = {
        "manifest_version": "1.0",
        "source_kind": mode,
        "version": version,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "entry_count": len(results),
        "entries": [result.registry_entry for result in results],
    }
    _write_json(registry_path, payload)
    return registry_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Register local txt/json sources into Mingle Reading source manifests.",
    )
    parser.add_argument("inputs", nargs="+", type=Path, help="Input txt/json files or directories.")
    parser.add_argument(
        "--mode",
        choices=("books", "persona_sources"),
        required=True,
        help="Registration mode for book text sources or persona source corpora.",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("scripts") / "registry_output",
        help="Root directory under which backend/assets/data/raw, backend/assets/data/processed, and backend/assets/data/manifests are created.",
    )
    parser.add_argument("--version", default="v001", help="Artifact version suffix, such as v001.")
    parser.add_argument("--title", default=None, help="Optional title override for single-source runs.")
    parser.add_argument("--author", default=None, help="Optional author override.")
    parser.add_argument("--persona-name", default=None, help="Optional persona or subject name for persona sources.")
    parser.add_argument("--source-type", default=None, help="Optional source_type override.")
    parser.add_argument("--copyright-status", default=None, help="Optional copyright_status override.")
    parser.add_argument("--language", default=None, help="Optional language override.")
    parser.add_argument("--license-note", default=None, help="Optional license note.")
    parser.add_argument("--notes", default=None, help="Optional notes stored in the raw record.")
    parser.add_argument(
        "--recursive",
        action="store_true",
        help="Recursively collect txt/json files when an input path is a directory.",
    )
    parser.add_argument(
        "--skip-hierarchical-build",
        action="store_true",
        help="For books mode, only register raw/manifests and skip processed dataset generation.",
    )
    parser.add_argument(
        "--retrieval-window",
        type=int,
        default=2,
        help="Forwarded to hierarchical_dataset_builder.py for books mode.",
    )
    parser.add_argument(
        "--quotes-per-chapter",
        type=int,
        default=2,
        help="Forwarded to hierarchical_dataset_builder.py for books mode.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    resolved_inputs = _resolve_inputs(args.inputs, recursive=args.recursive)
    if not resolved_inputs:
        raise ValueError("No txt/json input files found.")
    if len(resolved_inputs) > 1 and args.title:
        raise ValueError("--title can only be used when registering a single source.")
    if len(resolved_inputs) > 1 and args.persona_name:
        raise ValueError("--persona-name can only be used when registering a single persona source.")

    results: list[RegistrationResult] = []
    for input_path in resolved_inputs:
        if args.mode == "books":
            results.append(
                _register_book_source(
                    input_path,
                    output_root=args.output_root,
                    version=args.version,
                    title=args.title,
                    source_type=args.source_type,
                    copyright_status=args.copyright_status,
                    author=args.author,
                    language=args.language,
                    license_note=args.license_note,
                    notes=args.notes,
                    build_hierarchical=not args.skip_hierarchical_build,
                    retrieval_window=args.retrieval_window,
                    quotes_per_chapter=args.quotes_per_chapter,
                )
            )
            continue
        results.append(
            _register_persona_source(
                input_path,
                output_root=args.output_root,
                version=args.version,
                title=args.title,
                source_type=args.source_type,
                copyright_status=args.copyright_status,
                author=args.author,
                language=args.language,
                license_note=args.license_note,
                notes=args.notes,
                persona_name=args.persona_name,
            )
        )

    registry_path = _write_registry_batch(
        output_root=args.output_root,
        mode=args.mode,
        version=args.version,
        results=results,
    )
    summary = {
        "mode": args.mode,
        "output_root": str(args.output_root.as_posix()),
        "version": args.version,
        "entry_count": len(results),
        "registry_path": str(registry_path.as_posix()),
        "entries": [
            {
                "source_id": result.source_id,
                "title": result.title,
                "raw_record_path": str(result.raw_record_path.as_posix()),
                "manifest_path": str(result.manifest_path.as_posix()),
                "processed_manifest_path": (
                    str(result.processed_manifest_path.as_posix())
                    if result.processed_manifest_path
                    else None
                ),
            }
            for result in results
        ],
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
