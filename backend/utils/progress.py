"""
utils/progress.py
Optional per-agent progress reporting (wired by Celery during job execution).
"""

from __future__ import annotations
from contextvars import ContextVar
from typing import Callable

ProgressFn = Callable[[str, str, int], None]

_progress_fn: ContextVar[ProgressFn | None] = ContextVar("progress_fn", default=None)


def set_progress_callback(fn: ProgressFn | None) -> None:
    """Register a callback(job_id, step, progress_pct) for the current async context."""
    _progress_fn.set(fn)


def report_progress(job_id: str, step: str, progress: int) -> None:
    """Notify job tracker of workflow step (no-op if no callback registered)."""
    fn = _progress_fn.get()
    if fn and job_id:
        fn(job_id, step, min(100, max(0, progress)))


def clear_progress_callback() -> None:
    _progress_fn.set(None)
