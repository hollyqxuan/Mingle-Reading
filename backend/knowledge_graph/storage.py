from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from backend.config import GRAPHS_DIR
from backend.knowledge_graph.models import TemporalContextGraph


STORAGE_VERSION = "graph-store.v1"


def graph_path(book_id: str) -> Path:
    return GRAPHS_DIR / f"{book_id}.graph.json"


def graph_exists(book_id: str) -> bool:
    return graph_path(book_id).exists()


def save_graph(graph: TemporalContextGraph) -> None:
    GRAPHS_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC).isoformat()
    graph.metadata.setdefault("storage", {})
    graph.metadata["storage"].update(
        {
            "storage_version": STORAGE_VERSION,
            "saved_at": now,
            "graph_path": str(graph_path(graph.book_id)),
        }
    )
    graph.metadata.setdefault("graph_stats", graph.stats().model_dump())
    graph_path(graph.book_id).write_text(
        json.dumps(graph.model_dump(mode="json"), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def load_graph(book_id: str) -> TemporalContextGraph:
    payload = json.loads(graph_path(book_id).read_text(encoding="utf-8"))
    graph = TemporalContextGraph.model_validate(payload)
    graph.metadata.setdefault("storage", {})
    graph.metadata["storage"].setdefault("storage_version", STORAGE_VERSION)
    graph.metadata["storage"].setdefault("graph_path", str(graph_path(book_id)))
    graph.metadata["storage"].setdefault("loaded", True)
    graph.metadata.setdefault("graph_stats", graph.stats().model_dump())
    return graph


def load_graph_metadata(book_id: str) -> dict:
    graph = load_graph(book_id)
    return {
        "graph_id": graph.graph_id,
        "book_id": graph.book_id,
        "title": graph.title,
        "graph_version": graph.graph_version,
        "storage": graph.metadata.get("storage", {}),
        "stats": graph.stats().model_dump(),
    }
