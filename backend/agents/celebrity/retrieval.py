from __future__ import annotations

import math
import re

from backend.api.schemas import BookChunk, RetrievedContext


def tokenize(text: str) -> list[str]:
    return re.findall(r"[\w\u4e00-\u9fff]+", text.lower())


def score_chunk(query: str, chunk: BookChunk) -> float:
    query_terms = tokenize(query)
    if not query_terms:
        return 0.0
    chunk_terms = tokenize(chunk.text)
    if not chunk_terms:
        return 0.0
    overlap = sum(1 for term in query_terms if term in chunk_terms)
    density = overlap / math.sqrt(len(chunk_terms))
    return density


def retrieve_chunks(
    chunks: list[BookChunk],
    query: str,
    max_chapter: int,
    top_k: int = 4,
) -> list[RetrievedContext]:
    visible = [
        chunk
        for chunk in chunks
        if chunk.position.get("chapter_index", chunk.chapter_index) <= max_chapter
    ]
    ranked = sorted(
        visible,
        key=lambda chunk: score_chunk(query, chunk),
        reverse=True,
    )
    contexts: list[RetrievedContext] = []
    for chunk in ranked[:top_k]:
        score = round(score_chunk(query, chunk), 4)
        if score <= 0:
            continue
        contexts.append(
            RetrievedContext(
                chunk_id=chunk.chunk_id,
                chapter_index=chunk.chapter_index,
                paragraph_index=chunk.paragraph_index,
                score=score,
                text=chunk.text,
            )
        )
    return contexts
