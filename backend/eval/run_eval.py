from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.config import EXAMPLES_DIR, SCHEMAS_DIR
from backend.api.schemas import QuestionRequest
from backend.data_pipeline.ingest.parser import build_book_record
from backend.agents.celebrity.answering import build_answer
from backend.agents.celebrity.chapter_summary import summarize_chapter

BENCHMARKS_DIR = ROOT / "backend" / "benchmarks"


def ensure_demo_book():
    source = EXAMPLES_DIR / "mingle_demo_book.txt"
    return build_book_record("mingle_demo_book", source.read_text(encoding="utf-8"), source)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped:
            rows.append(json.loads(stripped))
    return rows


def _type_matches(value: Any, expected: str | list[str]) -> bool:
    expected_types = [expected] if isinstance(expected, str) else expected
    for expected_type in expected_types:
        if expected_type == "string" and isinstance(value, str):
            return True
        if expected_type == "integer" and isinstance(value, int) and not isinstance(value, bool):
            return True
        if expected_type == "number" and isinstance(value, (int, float)) and not isinstance(value, bool):
            return True
        if expected_type == "object" and isinstance(value, dict):
            return True
        if expected_type == "array" and isinstance(value, list):
            return True
        if expected_type == "null" and value is None:
            return True
    return False


def validate_against_schema(sample: dict[str, Any], schema: dict[str, Any], path: str = "$") -> list[str]:
    errors: list[str] = []
    expected_type = schema.get("type")
    if expected_type and not _type_matches(sample, expected_type):
        return [f"{path}: expected type {expected_type!r}"]

    enum = schema.get("enum")
    if enum is not None and sample not in enum:
        errors.append(f"{path}: expected one of {enum!r}")

    if isinstance(sample, dict):
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        for key in required:
            if key not in sample:
                errors.append(f"{path}.{key}: missing required field")
        if schema.get("additionalProperties") is False:
            extra_keys = sorted(set(sample) - set(properties))
            for key in extra_keys:
                errors.append(f"{path}.{key}: unexpected field")
        for key, value in sample.items():
            child_schema = properties.get(key)
            if child_schema:
                errors.extend(validate_against_schema(value, child_schema, f"{path}.{key}"))

    if isinstance(sample, list):
        item_schema = schema.get("items")
        if item_schema:
            for index, value in enumerate(sample):
                errors.extend(validate_against_schema(value, item_schema, f"{path}[{index}]"))

    if isinstance(sample, int):
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        if minimum is not None and sample < minimum:
            errors.append(f"{path}: expected >= {minimum}")
        if maximum is not None and sample > maximum:
            errors.append(f"{path}: expected <= {maximum}")

    if isinstance(sample, (int, float)) and not isinstance(sample, bool):
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        if minimum is not None and sample < minimum:
            errors.append(f"{path}: expected >= {minimum}")
        if maximum is not None and sample > maximum:
            errors.append(f"{path}: expected <= {maximum}")

    return errors


def evaluate_highlight_qa(book, schema: dict[str, Any]) -> dict[str, Any]:
    sample_files = sorted((BENCHMARKS_DIR / "highlight_qa").rglob("*.jsonl"))
    passed = 0
    failures: list[dict[str, Any]] = []

    for sample_file in sample_files:
        for sample in load_jsonl(sample_file):
            errors = validate_against_schema(sample, schema)
            response = build_answer(
                QuestionRequest(
                    book_id=sample["book_id"],
                    question=sample["question"],
                    highlight_text=sample["highlight"]["text"],
                    current_chapter=sample["reader_progress"]["current_chapter_index"],
                    persona_id=sample.get("persona_id") or "neutral",
                ),
                book.chunks,
            )
            returned_chunk_ids = {context.chunk_id for context in response.contexts}
            expected_chunk_ids = set(sample["support_chunk_ids"])
            ok = (
                not errors
                and response.safe
                and bool(response.answer.strip())
                and expected_chunk_ids.issubset(returned_chunk_ids)
            )
            if ok:
                passed += 1
            else:
                failures.append(
                    {
                        "sample_id": sample["sample_id"],
                        "errors": errors,
                        "response_reason": response.reason,
                        "returned_chunk_ids": sorted(returned_chunk_ids),
                    }
                )

    sample_count = passed + len(failures)
    return {
        "sample_count": sample_count,
        "passed": passed,
        "failed": len(failures),
        "failures": failures,
    }


def _anti_spoiler_expectation_met(expected_behavior: str, response) -> bool:
    if expected_behavior == "refuse_future_plot":
        return response.safe is False and response.reason == "question_requests_future_plot"
    if expected_behavior in {"answer_within_boundary", "acknowledge_uncertainty", "reflect_on_known_text_only"}:
        return response.safe is True and bool(response.answer.strip())
    return False


def evaluate_anti_spoiler(book, schema: dict[str, Any]) -> dict[str, Any]:
    sample_files = sorted((BENCHMARKS_DIR / "anti_spoiler").rglob("*.jsonl"))
    passed = 0
    failures: list[dict[str, Any]] = []

    for sample_file in sample_files:
        for sample in load_jsonl(sample_file):
            errors = validate_against_schema(sample, schema)
            response = build_answer(
                QuestionRequest(
                    book_id=sample["book_id"],
                    question=sample["prompt"],
                    current_chapter=sample["reader_progress"]["allowed_chapter_max"],
                    persona_id="neutral",
                ),
                book.chunks,
            )
            ok = not errors and _anti_spoiler_expectation_met(
                sample["gold_label"]["expected_behavior"],
                response,
            )
            if ok:
                passed += 1
            else:
                failures.append(
                    {
                        "sample_id": sample["sample_id"],
                        "errors": errors,
                        "response_safe": response.safe,
                        "response_reason": response.reason,
                    }
                )

    sample_count = passed + len(failures)
    return {
        "sample_count": sample_count,
        "passed": passed,
        "failed": len(failures),
        "failures": failures,
    }


def evaluate_chapter_summary(book) -> dict[str, Any]:
    sample_files = sorted((BENCHMARKS_DIR / "chapter_summary").rglob("*.jsonl"))
    passed = 0
    failures: list[dict[str, Any]] = []

    for sample_file in sample_files:
        for sample in load_jsonl(sample_file):
            summary = summarize_chapter(
                book,
                current_chapter=sample["current_chapter_index"],
                persona_id=sample.get("persona_id", "neutral"),
            )
            text = summary.summary
            missing = [phrase for phrase in sample["expected_phrases"] if phrase not in text]
            leaked = [phrase for phrase in sample.get("forbidden_phrases", []) if phrase in text]
            ok = not missing and not leaked
            if ok:
                passed += 1
            else:
                failures.append(
                    {
                        "sample_id": sample["sample_id"],
                        "missing_phrases": missing,
                        "forbidden_phrases_found": leaked,
                    }
                )

    sample_count = passed + len(failures)
    return {
        "sample_count": sample_count,
        "passed": passed,
        "failed": len(failures),
        "failures": failures,
    }


def run_evaluation() -> dict[str, Any]:
    book = ensure_demo_book()
    highlight_schema = json.loads((SCHEMAS_DIR / "highlight_qa.schema.json").read_text(encoding="utf-8"))
    anti_spoiler_schema = json.loads((SCHEMAS_DIR / "anti_spoiler_eval.schema.json").read_text(encoding="utf-8"))

    highlight_result = evaluate_highlight_qa(book, highlight_schema)
    anti_spoiler_result = evaluate_anti_spoiler(book, anti_spoiler_schema)
    summary_result = evaluate_chapter_summary(book)

    failed = (
        highlight_result["failed"]
        + anti_spoiler_result["failed"]
        + summary_result["failed"]
    )
    passed = (
        highlight_result["passed"]
        + anti_spoiler_result["passed"]
        + summary_result["passed"]
    )
    return {
        "book_id": book.book_id,
        "highlight_qa": highlight_result,
        "anti_spoiler": anti_spoiler_result,
        "chapter_summary": summary_result,
        "overall": {
            "passed": passed,
            "failed": failed,
        },
    }


def main() -> None:
    print(json.dumps(run_evaluation(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

