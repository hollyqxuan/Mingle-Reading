from __future__ import annotations

from dataclasses import dataclass, field

from backend.api.schemas import BookChunk


@dataclass(slots=True)
class ExtractionWindow:
    window_id: str
    chapter_index: int
    core_chunks: list[BookChunk] = field(default_factory=list)
    core_text: str = ""
    core_token_count: int = 0
    prev_context_text: str = ""
    prev_context_token_count: int = 0


def _looks_like_chapter_heading(line: str) -> bool:
    import re

    stripped = line.strip()
    if not stripped:
        return False
    patterns = [
        re.compile(r"^\s*Chapter[\s_\-]*\d+.*$", re.IGNORECASE),
        re.compile(r"^\s*第\s*[0-9一二三四五六七八九十百千两零]+\s*[章节回部卷].*$"),
    ]
    return any(p.match(stripped) for p in patterns)


def _is_scene_separator(text: str) -> bool:
    import re

    stripped = text.strip()
    if not stripped:
        return True
    if re.fullmatch(r"[-_=*~·•]{3,}", stripped):
        return True
    lowered = stripped.lower()
    if lowered.startswith("scene "):
        return True
    return stripped in {"场景", "幕间"}


def _topic_shift_marker(text: str) -> bool:
    lowered = text.strip().lower()
    markers = (
        "later", "meanwhile", "suddenly", "the next day", "at dawn", "at night",
        "回到", "后来", "与此同时", "忽然", "第二天", "当天夜里",
    )
    return any(lowered.startswith(marker) for marker in markers)


def _is_force_split(chunk: BookChunk) -> bool:
    first_line = chunk.text.split("\n")[0].strip() if chunk.text else ""
    return (
        _looks_like_chapter_heading(first_line)
        or _is_scene_separator(chunk.text)
        or _topic_shift_marker(chunk.text)
    )


def _approximate_token_count(text: str) -> int:
    import re

    latin_words = re.findall(r"[A-Za-z0-9_]+", text)
    cjk_chars = re.findall(r"[一-鿿]", text)
    return max(1, len(latin_words) + len(cjk_chars))


def build_extraction_windows(
    chunks: list[BookChunk],
    *,
    window_size: int = 800,
    step_size: int = 400,
    lead_in_tokens: int = 500,
) -> list[ExtractionWindow]:
    """Build sliding windows with a fixed lead-in context for every window.

    Each window gets:
    - core_text: the current window's chunks (up to window_size tokens)
    - prev_context_text: ~lead_in_tokens of text immediately before the core.
      This is INDEPENDENT of chunk boundaries — every window after the first
      gets a lead-in, even if it overlaps with the previous window.
    """
    sorted_chunks = sorted(chunks, key=lambda c: (c.chapter_index, c.paragraph_index))
    windows: list[ExtractionWindow] = []
    pos = 0

    # Build a flat text array for token-level context building
    chunk_texts: list[str] = [c.text for c in sorted_chunks]
    chunk_tokens: list[int] = [
        int((c.metadata or {}).get("packet_token_count", _approximate_token_count(c.text)) or 0)
        for c in sorted_chunks
    ]

    def _build_lead_in(start_chunk_index: int) -> tuple[str, int]:
        """Collect ~lead_in_tokens of text immediately before start_chunk_index."""
        collected: list[str] = []
        collected_tokens = 0
        idx = start_chunk_index - 1
        while idx >= 0 and collected_tokens < lead_in_tokens:
            # Stop at chapter boundary
            if sorted_chunks[idx].chapter_index != sorted_chunks[start_chunk_index].chapter_index:
                break
            ct = chunk_tokens[idx]
            collected.insert(0, chunk_texts[idx])
            collected_tokens += ct
            idx -= 1
        if collected:
            return "\n\n".join(collected), collected_tokens
        return "", 0

    while pos < len(sorted_chunks):
        core_chunks: list[BookChunk] = []
        core_tokens = 0
        end = pos

        while end < len(sorted_chunks):
            c = sorted_chunks[end]
            ct = chunk_tokens[end]
            if core_tokens + ct > window_size:
                break
            if end > pos and c.chapter_index != sorted_chunks[pos].chapter_index:
                break
            if core_chunks and _is_force_split(c):
                break
            core_chunks.append(c)
            core_tokens += ct
            end += 1

        if not core_chunks:
            c = sorted_chunks[pos]
            core_chunks = [c]
            core_tokens = chunk_tokens[pos]
            end = pos + 1

        core_text = "\n\n".join(c.text for c in core_chunks)

        # Build fixed lead-in context (every window except the first)
        prev_context_text = ""
        prev_context_token_count = 0
        if pos > 0 and sorted_chunks[pos].chapter_index > 0:
            prev_context_text, prev_context_token_count = _build_lead_in(pos)

        chapter_idx = sorted_chunks[pos].chapter_index
        window_id = f"window_c{chapter_idx:03d}_w{len(windows):03d}"

        windows.append(
            ExtractionWindow(
                window_id=window_id,
                chapter_index=chapter_idx,
                core_chunks=core_chunks,
                core_text=core_text,
                core_token_count=core_tokens,
                prev_context_text=prev_context_text,
                prev_context_token_count=prev_context_token_count,
            )
        )

        # Advance by ~2/3 of the window's chunks (overlap).
        # Cross-boundary continuity comes from overlapping chunks.
        adva = 1 if len(core_chunks) <= 1 else max(1, len(core_chunks) * 2 // 3)
        pos = pos + adva

    return windows
