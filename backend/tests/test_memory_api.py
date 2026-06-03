from __future__ import annotations

from fastapi.testclient import TestClient

from backend.api import app as app_module
from backend.api.schemas import BookChunk, BookRecord
from backend.memory import service as memory_service


def _book() -> BookRecord:
    return BookRecord(
        book_id="unit-memory-book",
        title="Unit Memory Book",
        source_path="unit.txt",
        chapter_count=1,
        chunks=[
            BookChunk(
                chunk_id="chunk-1",
                book_id="unit-memory-book",
                chapter_id="chapter-001",
                paragraph_id="paragraph-001",
                chapter_index=1,
                paragraph_index=1,
                text="Lin opened the old notebook. Mara asked Lin why the library felt different.",
                candidate_characters=["Lin", "Mara"],
                position={"chapter_index": 1},
            )
        ],
    )


def test_memory_status_degrades_without_index(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(app_module, "get_or_build_book", lambda book_id: _book())
    monkeypatch.setattr(memory_service, "INDEXES_DIR", tmp_path)
    monkeypatch.setattr(memory_service, "graph_exists", lambda book_id: False)
    client = TestClient(app_module.app)

    response = client.get("/api/books/unit-memory-book/memory/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "missing"
    assert "memory_index_missing" in payload["degraded_reasons"]


def test_memory_map_builds_heuristic_registry(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(app_module, "get_or_build_book", lambda book_id: _book())
    monkeypatch.setattr(memory_service, "INDEXES_DIR", tmp_path)
    monkeypatch.setattr(memory_service, "graph_exists", lambda book_id: False)
    client = TestClient(app_module.app)

    response = client.get(
        "/api/books/unit-memory-book/memory/map",
        params={"scope": "passage", "chapter": 1, "paragraph": 1},
    )

    assert response.status_code == 200
    payload = response.json()
    labels = {node["label"] for node in payload["nodes"]}
    assert {"Lin", "Mara"}.issubset(labels)
    assert payload["stats"]["node_count"] >= 2
