"""
api/workflow_runner.py
Run the workflow inline (no Celery worker) — used on Windows where prefork pool fails.
Stores results in the same Redis backend Celery uses so /api/jobs/{id} keeps working.
"""

from __future__ import annotations

import asyncio
import sys
import traceback
import structlog
from celery import states

from api.celery_app import celery_app, set_job_progress, _get_redis
from models.schemas import Priority

logger = structlog.get_logger()


def use_inline_worker() -> bool:
    """Windows Celery prefork/spawn pool is unreliable; inline worker is the default there."""
    import os
    if os.getenv("USE_CELERY", "").lower() in ("1", "true", "yes"):
        return False
    if os.getenv("INLINE_WORKFLOW", "").lower() in ("1", "true", "yes"):
        return True
    return sys.platform == "win32"


def run_workflow_sync(
    job_id: str,
    url: str,
    brand_name: str | None = None,
    extra_instructions: str | None = None,
    priority: Priority | None = None,
    batch_id: str | None = None,
) -> None:
    """Execute workflow in-process and persist status to Celery's Redis result backend."""
    from graph import run_workflow
    from utils.progress import set_progress_callback, clear_progress_callback
    from utils.storage import persist_workflow_state

    def on_progress(jid: str, step: str, pct: int):
        if jid == job_id:
            set_job_progress(job_id, step, pct)
            celery_app.backend.store_result(
                job_id,
                {"step": step, "progress": pct},
                states.STARTED,
                request_meta={"task_name": "inline_workflow"},
            )

    set_progress_callback(on_progress)
    set_job_progress(job_id, "starting", 5)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        celery_app.backend.store_result(
            job_id, {"step": "starting", "progress": 5}, states.STARTED
        )
        final_state = loop.run_until_complete(
            run_workflow(
                url,
                job_id=job_id,
                brand_name=brand_name,
                extra_instructions=extra_instructions,
                priority=priority,
            )
        )
        # `final_state` may be a Pydantic `BaseModel` (has `model_dump`) or
        # already a plain `dict` depending on LangGraph internals. Handle both.
        def serialize_obj(o):
            """Recursively convert Pydantic models, enums, and datetimes to
            JSON-serializable primitives.
            """
            from pydantic import BaseModel
            import datetime
            import enum

            if isinstance(o, BaseModel):
                return o.model_dump(mode="json")
            if isinstance(o, dict):
                return {k: serialize_obj(v) for k, v in o.items()}
            if isinstance(o, (list, tuple)):
                return [serialize_obj(v) for v in o]
            if isinstance(o, enum.Enum):
                return o.value
            if isinstance(o, (datetime.datetime, datetime.date)):
                return o.isoformat()
            return o

        if hasattr(final_state, "model_dump"):
            result = final_state.model_dump(mode="json")
        elif isinstance(final_state, dict):
            result = serialize_obj(final_state)
        else:
            # Fallback: coerce to a JSON-serializable structure
            import json

            try:
                # Try a round-trip through JSON to ensure serializability
                result = json.loads(json.dumps(final_state, default=str))
            except Exception:
                result = {"state": str(final_state)}
        persist_workflow_state(final_state)
        set_job_progress(job_id, "completed", 100)
        celery_app.backend.store_result(job_id, result, states.SUCCESS)
        if batch_id:
            try:
                r = _get_redis()
                r.hincrby(f"batch:{batch_id}", "completed", 1)
                r.hincrby(f"batch:{batch_id}", "running", -1)
            except Exception:
                pass
        logger.info("inline_workflow_complete", job_id=job_id)
    except Exception as e:
        logger.error("inline_workflow_failed", job_id=job_id, error=str(e))
        set_job_progress(job_id, "failed", 0)
        celery_app.backend.mark_as_failure(
            job_id,
            exc=e,
            traceback=traceback.format_exc(),
        )
        if batch_id:
            try:
                r = _get_redis()
                r.hincrby(f"batch:{batch_id}", "failed", 1)
                r.hincrby(f"batch:{batch_id}", "running", -1)
            except Exception:
                pass
        raise
    finally:
        loop.close()
        clear_progress_callback()
