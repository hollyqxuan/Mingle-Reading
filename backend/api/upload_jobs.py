from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock
from typing import Any
from uuid import uuid4


@dataclass
class UploadJobState:
    job_id: str
    status: str = "queued"
    stage: str = "queued"
    title: str = "Upload queued"
    message: str = "等待后台开始处理上传文本。"
    percent: int = 0
    processed_snippets: int = 0
    total_snippets: int = 0
    current_snippet_id: str = ""
    current_chapter_index: int = 0
    current_paragraph_index: int = 0
    book_id: str = ""
    book_title: str = ""
    chunk_count: int = 0
    chapter_count: int = 0
    error: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "status": self.status,
            "stage": self.stage,
            "title": self.title,
            "message": self.message,
            "percent": self.percent,
            "processed_snippets": self.processed_snippets,
            "total_snippets": self.total_snippets,
            "current_snippet_id": self.current_snippet_id,
            "current_chapter_index": self.current_chapter_index,
            "current_paragraph_index": self.current_paragraph_index,
            "book_id": self.book_id,
            "book_title": self.book_title,
            "chunk_count": self.chunk_count,
            "chapter_count": self.chapter_count,
            "error": self.error,
            "details": self.details,
        }


class UploadJobRegistry:
    def __init__(self) -> None:
        self._lock = Lock()
        self._jobs: dict[str, UploadJobState] = {}

    def create(self) -> UploadJobState:
        job = UploadJobState(job_id=f"upload-{uuid4().hex}")
        with self._lock:
            self._jobs[job.job_id] = job
        return job

    def get(self, job_id: str) -> UploadJobState | None:
        with self._lock:
            return self._jobs.get(job_id)

    def update(self, job_id: str, **fields: Any) -> UploadJobState:
        with self._lock:
            job = self._jobs[job_id]
            for key, value in fields.items():
                setattr(job, key, value)
            return job


upload_job_registry = UploadJobRegistry()
