from __future__ import annotations

import math
import re
from collections.abc import Iterable

BLOCKED_LABELS = {"the", "when", "chapter", "unknown_characters"}


def tokenize(text: str) -> list[str]:
    return re.findall(r"[\w\u4e00-\u9fff]+", text.lower())


def normalize_label(value: str) -> str:
    lowered = value.strip().lower()
    lowered = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "_", lowered)
    return lowered.strip("_")


def keyword_score(query: str, text: str) -> float:
    query_terms = tokenize(query)
    text_terms = tokenize(text)
    if not query_terms or not text_terms:
        return 0.0
    overlap = sum(1 for term in query_terms if term in text_terms)
    if overlap <= 0:
        return 0.0
    return overlap / math.sqrt(len(text_terms))


def unique_preserve_order(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        normalized = normalize_label(value)
        if not normalized or normalized in seen or normalized in BLOCKED_LABELS:
            continue
        seen.add(normalized)
        ordered.append(normalized)
    return ordered
