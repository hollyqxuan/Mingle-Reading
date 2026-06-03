from __future__ import annotations

from dataclasses import dataclass


SPOILER_HINTS = [
    "结局",
    "最后",
    "后来",
    "真相",
    "凶手",
    "反转",
    "最终",
    "who dies",
    "ending",
    "later",
    "final",
    "spoiler",
]


@dataclass
class SafetyDecision:
    safe: bool
    reason: str


def is_spoiler_question(question: str) -> SafetyDecision:
    lowered = question.lower()
    for hint in SPOILER_HINTS:
        if hint in lowered:
            return SafetyDecision(
                safe=False,
                reason="question_requests_future_plot",
            )
    return SafetyDecision(safe=True, reason="within_visible_scope")

