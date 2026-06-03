from __future__ import annotations

import json
from pathlib import Path

from backend.config import BOOKS_DIR
from backend.api.schemas import BookRecord


def get_book_path(book_id: str) -> Path:
    return BOOKS_DIR / f"{book_id}.json"


def save_book(record: BookRecord) -> None:
    path = get_book_path(record.book_id)
    path.write_text(
        json.dumps(record.model_dump(mode="json"), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def load_book(book_id: str) -> BookRecord:
    path = get_book_path(book_id)
    payload = json.loads(path.read_text(encoding="utf-8"))
    return BookRecord.model_validate(payload)


def list_books() -> list[dict[str, str]]:
    books: list[dict[str, str]] = []
    for path in sorted(BOOKS_DIR.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        books.append({"book_id": payload["book_id"], "title": payload["title"]})
    return books
