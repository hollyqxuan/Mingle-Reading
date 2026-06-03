from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from backend.config import LOGS_DIR


class GraphBuildLogger:
    """Writes a structured JSON Lines log for each knowledge-graph build."""

    def __init__(self, book_id: str, title: str) -> None:
        self.book_id = book_id
        self.title = title
        self.log_path = LOGS_DIR / f"{book_id}.build.jsonl"
        self._entries: list[dict[str, Any]] = []
        self._started_at: str | None = None

    # ---- public API ----

    def build_start(self, total_chunks: int, extraction_backend: str) -> None:
        self._started_at = _utc_now()
        self._emit("build_start", total_chunks=total_chunks, extraction_backend=extraction_backend)

    def episode_start(self, chunk_id: str, chapter: int, paragraph: int,
                      text: str, token_count: int, source_para_count: int,
                      is_merged: bool) -> None:
        self._emit("episode_start", chunk_id=chunk_id, chapter=chapter,
                   paragraph=paragraph, text_preview=text[:120],
                   token_count=token_count, source_paragraph_count=source_para_count,
                   is_merged_packet=is_merged)

    def llm_decision(self, chunk_id: str, called: bool, score: int,
                     reasons: list[str]) -> None:
        self._emit("llm_decision", chunk_id=chunk_id, llm_called=called,
                   gate_score=score, gate_reasons=reasons)

    def llm_response(self, chunk_id: str, entity_count: int, fact_count: int,
                     entities: list[dict[str, Any]] | None = None,
                     facts: list[dict[str, Any]] | None = None,
                     raw_response: str = "") -> None:
        self._emit("llm_response", chunk_id=chunk_id,
                   llm_entity_candidates=entity_count, llm_fact_candidates=fact_count,
                   entities=entities or [], facts=facts or [],
                   raw_response=raw_response[:500])

    def episode_end(self, chunk_id: str, extraction_mode: str,
                    entity_count: int, relation_count: int,
                    entity_names: list[str] | None = None,
                    relations: list[dict[str, str]] | None = None) -> None:
        self._emit("episode_end", chunk_id=chunk_id, extraction_mode=extraction_mode,
                   entity_count=entity_count, relation_count=relation_count,
                   entity_names=entity_names or [], relations=relations or [])

    def build_end(self, stats: dict[str, Any]) -> None:
        self._emit("build_end", **stats)

    # ---- internal ----

    def _emit(self, event: str, **fields: Any) -> None:
        entry: dict[str, Any] = {"event": event, "ts": _utc_now(), **fields}
        self._entries.append(entry)

    def flush(self) -> None:
        """Write all buffered entries to disk."""
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        with open(self.log_path, "a", encoding="utf-8") as fh:
            for entry in self._entries:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        self._entries.clear()

    def close(self) -> None:
        self.flush()

    def __enter__(self) -> GraphBuildLogger:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
