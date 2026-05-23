"""
api/celery_app.py
Celery configuration for async job processing.
Supports both single-URL jobs and bulk CSV batch processing.
"""

from __future__ import annotations

import utils.env  # noqa: F401 — load .env before other imports use os.environ

import os
import json
import asyncio
import structlog
from celery import Celery
from models.schemas import Priority

logger = structlog.get_logger()

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

celery_app = Celery(
    "ai_creative_workflow",
    broker=REDIS_URL,
    backend=REDIS_URL,
    include=["api.celery_app"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    result_expires=86400 * 7,      # Keep results for 7 days
    worker_prefetch_multiplier=1,   # One task at a time per worker
    task_acks_late=True,            # Ack after completion (safer for retries)
    task_soft_time_limit=600,       # 10 min soft limit
    task_time_limit=900,            # 15 min hard limit
    # Priority queues
    task_queues={
        "high": {"exchange": "high", "routing_key": "high"},
        "normal": {"exchange": "normal", "routing_key": "normal"},
        "low": {"exchange": "low", "routing_key": "low"},
    },
    task_default_queue="normal",
    task_routes={
        "api.celery_app.process_single_url": {"queue": "normal"},
        "api.celery_app.process_bulk_batch": {"queue": "normal"},
    },
)

# ─── Job status store (Redis-backed) ─────────────────────────────────────────

def _get_redis():
    import redis
    return redis.from_url(REDIS_URL, decode_responses=True)


def set_job_progress(job_id: str, step: str, progress: int):
    try:
        r = _get_redis()
        r.hset(f"job:{job_id}", mapping={"step": step, "progress": progress})
        r.expire(f"job:{job_id}", 86400 * 7)
    except Exception as e:
        logger.warning("redis_progress_failed", error=str(e))


def get_job_progress(job_id: str) -> dict:
    try:
        r = _get_redis()
        data = r.hgetall(f"job:{job_id}")
        return data or {}
    except Exception:
        return {}


# ─── Celery Tasks ─────────────────────────────────────────────────────────────

@celery_app.task(bind=True, name="api.celery_app.process_single_url")
def process_single_url(
    self,
    job_id: str,
    url: str,
    brand_name: str | None = None,
    extra_instructions: str | None = None,
    priority: Priority | None = None,
):
    """
    Celery task: Run the full multi-agent workflow for a single URL.
    """
    logger.info("task_started", job_id=job_id, url=url)
    set_job_progress(job_id, "starting", 5)

    # Progress hook (updates Redis as each agent completes)
    original_update = self.update_state

    def update_progress(step: str, pct: int):
        set_job_progress(job_id, step, pct)
        original_update(state="PROGRESS", meta={"step": step, "progress": pct})

    try:
        # Import here to avoid circular imports at module level
        from graph import run_workflow
        from utils.progress import set_progress_callback, clear_progress_callback
        from utils.storage import persist_workflow_state

        def on_progress(jid: str, step: str, pct: int):
            if jid == job_id:
                update_progress(step, pct)

        set_progress_callback(on_progress)
        update_progress("product_research", 10)

        # Run async workflow in new event loop
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            final_state = loop.run_until_complete(
                run_workflow(
                    url,
                    job_id=job_id,
                    brand_name=brand_name,
                    extra_instructions=extra_instructions,
                    priority=priority,
                )
            )
        finally:
            loop.close()
            clear_progress_callback()

        update_progress("completed", 100)
        persist_workflow_state(final_state)
        result = final_state.model_dump(mode="json")
        logger.info("task_completed", job_id=job_id)
        return result

    except Exception as e:
        logger.error("task_failed", job_id=job_id, error=str(e))
        set_job_progress(job_id, "failed", 0)
        from utils.progress import clear_progress_callback
        clear_progress_callback()
        raise


@celery_app.task(bind=True, name="api.celery_app.process_bulk_batch")
def process_bulk_batch(self, batch_id: str, jobs: list[dict]):
    """
    Celery task: Dispatch individual URL tasks for a bulk CSV batch.
    Tracks batch-level progress in Redis.
    """
    logger.info("bulk_batch_started", batch_id=batch_id, total=len(jobs))

    r = _get_redis()
    r.hset(f"batch:{batch_id}", mapping={
        "total": len(jobs),
        "completed": 0,
        "failed": 0,
        "running": 0,
        "pending": len(jobs),
    })
    r.expire(f"batch:{batch_id}", 86400 * 7)

    # Dispatch individual tasks
    task_ids = []
    for job in jobs:
        queue = "high" if job.get("priority") == "high" else "normal"
        r.hincrby(f"batch:{batch_id}", "pending", -1)
        r.hincrby(f"batch:{batch_id}", "running", 1)
        task = process_single_url.apply_async(
            kwargs={
                "job_id": job["job_id"],
                "url": job["url"],
                "brand_name": job.get("brand_name"),
                "extra_instructions": job.get("extra_instructions"),
                "priority": job.get("priority"),
            },
            queue=queue,
            link=_on_job_complete.s(batch_id=batch_id),
            link_error=_on_job_error.s(batch_id=batch_id),
        )
        task_ids.append(task.id)
        r.rpush(f"batch:{batch_id}:jobs", job["job_id"])

    logger.info("bulk_batch_dispatched", batch_id=batch_id, tasks=len(task_ids))
    return {"batch_id": batch_id, "task_ids": task_ids}


@celery_app.task(name="api.celery_app._on_job_complete")
def _on_job_complete(result, batch_id: str):
    """Callback: increment completed counter when a batch job succeeds."""
    try:
        r = _get_redis()
        r.hincrby(f"batch:{batch_id}", "completed", 1)
        r.hincrby(f"batch:{batch_id}", "running", -1)
    except Exception:
        pass


@celery_app.task(name="api.celery_app._on_job_error")
def _on_job_error(request, exc, traceback, batch_id: str):
    """Callback: increment failed counter when a batch job errors."""
    try:
        r = _get_redis()
        r.hincrby(f"batch:{batch_id}", "failed", 1)
        r.hincrby(f"batch:{batch_id}", "running", -1)
    except Exception:
        pass