"""
api/main.py
FastAPI application — main entry point.
Handles single URL generation, bulk CSV upload, and job status polling.
"""

from __future__ import annotations

import utils.env  # noqa: F401 — load .env before other imports use os.environ

import os
import csv
import io
import uuid
import json
import structlog
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, UploadFile, File, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse

from models.schemas import (
    GenerateRequest, JobStatusResponse, JobStatus,
    BulkUploadResponse, BulkStatusResponse, CSVRow, Priority
)
from api.celery_app import (
    celery_app, process_single_url, process_bulk_batch,
    get_job_progress, set_job_progress, _get_redis,
)
from api.workflow_runner import use_inline_worker, run_workflow_sync

logger = structlog.get_logger()

OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "./outputs"))
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(
    title="AI Product Creative Generation Workflow",
    description="Multi-agent system for generating product marketing images and videos",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve generated outputs statically
app.mount("/outputs", StaticFiles(directory=str(OUTPUT_DIR)), name="outputs")


# ─── Health check ─────────────────────────────────────────────────────────────

def _normalize_url(raw_url: str) -> str:
    url = (raw_url or "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="URL is required")
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise HTTPException(status_code=400, detail="URL must start with http:// or https://")
    return url

@app.get("/health")
async def health():
    return {"status": "ok", "version": "1.0.0"}


# ─── Single URL generation ────────────────────────────────────────────────────

@app.post("/api/generate", response_model=JobStatusResponse)
async def generate(request: GenerateRequest, background_tasks: BackgroundTasks):
    """
    Start the multi-agent creative generation workflow for a single product URL.
    Returns a job_id for polling status.

    On Windows, runs inline in the API process (Celery prefork pool is unreliable).
    On Linux/Docker, uses Celery unless INLINE_WORKFLOW=1 or USE_CELERY=0.
    """
    url = _normalize_url(request.url)

    job_id = str(uuid.uuid4())
    logger.info(
        "generate_request",
        job_id=job_id,
        url=url,
        inline_worker=use_inline_worker(),
    )

    set_job_progress(job_id, "queued", 0)

    if use_inline_worker():
        # No Celery worker required — Redis still used for job status/results
        background_tasks.add_task(
            run_workflow_sync,
            job_id,
            url,
            request.brand_name,
            request.extra_instructions,
            request.priority.value,
        )
    else:
        queue = "high" if request.priority == Priority.HIGH else "normal"
        process_single_url.apply_async(
            kwargs={
                "job_id": job_id,
                "url": url,
                "brand_name": request.brand_name,
                "extra_instructions": request.extra_instructions,
                "priority": request.priority.value,
            },
            task_id=job_id,
            queue=queue,
        )

    return JobStatusResponse(
        job_id=job_id,
        status=JobStatus.PENDING,
        progress=0,
        current_step="queued",
    )


# ─── Job status polling ───────────────────────────────────────────────────────

def _safe_task_state(job_id: str) -> tuple[str, Any]:
    """Read Celery task state; tolerate malformed legacy failure payloads in Redis."""
    result = celery_app.AsyncResult(job_id)
    try:
        return result.state, result.info
    except Exception as e:
        logger.warning("celery_result_decode_failed", job_id=job_id, error=str(e))
        progress = get_job_progress(job_id)
        if progress.get("step") == "failed":
            return "FAILURE", progress.get("error") or str(e)
        if int(progress.get("progress", 0)) >= 100:
            return "SUCCESS", None
        if int(progress.get("progress", 0)) > 0:
            return "STARTED", progress
        return "PENDING", None


@app.get("/api/jobs/{job_id}", response_model=JobStatusResponse)
async def get_job_status(job_id: str):
    """Poll the status and progress of a generation job."""
    progress_data = get_job_progress(job_id)
    state, info = _safe_task_state(job_id)

    if state == "PENDING":
        # Inline worker may have started before Celery result state updates
        prog = int(progress_data.get("progress", 0))
        step = progress_data.get("step", "queued")
        if prog > 0 or step not in ("", "queued"):
            return JobStatusResponse(
                job_id=job_id,
                status=JobStatus.RUNNING,
                progress=prog,
                current_step=step,
            )
        return JobStatusResponse(
            job_id=job_id,
            status=JobStatus.PENDING,
            progress=0,
            current_step="queued",
        )

    if state in ("STARTED", "PROGRESS"):
        meta = info if isinstance(info, dict) else {}
        return JobStatusResponse(
            job_id=job_id,
            status=JobStatus.RUNNING,
            progress=meta.get("progress", int(progress_data.get("progress", 0))),
            current_step=meta.get("step", progress_data.get("step", "processing")),
        )

    if state == "SUCCESS":
        result = celery_app.AsyncResult(job_id)
        try:
            state_dict = result.result if isinstance(result.result, dict) else {}
        except Exception:
            state_dict = {}
        return JobStatusResponse(
            job_id=job_id,
            status=JobStatus.COMPLETED,
            progress=100,
            current_step="completed",
            result=state_dict,
        )

    if state == "FAILURE":
        err = info
        if isinstance(info, dict):
            err = info.get("exc_message") or info.get("error") or str(info)
        if not err and progress_data.get("step") == "failed":
            err = "Workflow failed (see API logs)"
        return JobStatusResponse(
            job_id=job_id,
            status=JobStatus.FAILED,
            progress=0,
            current_step="failed",
            error=str(err) if err else "Unknown error",
        )

    return JobStatusResponse(
        job_id=job_id,
        status=JobStatus.RUNNING,
        progress=int(progress_data.get("progress", 10)),
        current_step=progress_data.get("step", "processing"),
    )


@app.get("/api/results/{job_id}")
async def get_results(job_id: str):
    """Get the full result payload for a completed job."""
    result = celery_app.AsyncResult(job_id)

    if result.state != "SUCCESS":
        raise HTTPException(status_code=404, detail=f"Job {job_id} not completed yet")

    return JSONResponse(content=result.result)


# ─── Bulk CSV upload ──────────────────────────────────────────────────────────

@app.post("/api/bulk", response_model=BulkUploadResponse)
async def bulk_upload(background_tasks: BackgroundTasks, file: UploadFile = File(...)):
    """
    Upload a CSV file containing multiple product URLs for bulk processing.
    
    CSV format (header required):
        url, brand_name (optional), priority (optional), extra_instructions (optional)
    """
    if not file.filename.endswith(".csv"):
        raise HTTPException(status_code=400, detail="Only CSV files are accepted")

    contents = await file.read()
    text = contents.decode("utf-8-sig")  # Handle BOM

    reader = csv.DictReader(io.StringIO(text))
    rows: list[CSVRow] = []
    errors = []

    for i, row in enumerate(reader, 1):
        raw_url = row.get("url", "").strip() or row.get("product_url", "").strip()
        if not raw_url:
            errors.append(f"Row {i}: missing URL")
            continue
        try:
            url = _normalize_url(raw_url)
        except HTTPException as e:
            errors.append(f"Row {i}: {e.detail}")
            continue
        try:
            raw_priority = (row.get("priority", "normal") or "normal").strip().lower()
            try:
                priority = Priority(raw_priority)
            except Exception:
                raise ValueError(f"Invalid priority '{raw_priority}'")
            rows.append(CSVRow(
                url=url,
                brand_name=row.get("brand_name", "").strip() or None,
                priority=priority,
                extra_instructions=row.get("extra_instructions", "").strip() or None,
            ))
        except Exception as e:
            errors.append(f"Row {i}: {str(e)}")

    if not rows:
        raise HTTPException(status_code=400, detail=f"No valid URLs found. Errors: {errors}")

    batch_id = str(uuid.uuid4())
    jobs = [
        {
            "job_id": str(uuid.uuid4()),
            "url": row.url,
            "priority": row.priority.value,
            "brand_name": row.brand_name,
            "extra_instructions": row.extra_instructions,
        }
        for row in rows
    ]

    logger.info("bulk_upload", batch_id=batch_id, total=len(jobs), errors=len(errors))

    if use_inline_worker():
        r = _get_redis()
        r.hset(f"batch:{batch_id}", mapping={
            "total": len(jobs),
            "completed": 0,
            "failed": 0,
            "running": 0,
            "pending": len(jobs),
        })
        r.expire(f"batch:{batch_id}", 86400 * 7)

        for job in jobs:
            r.hincrby(f"batch:{batch_id}", "pending", -1)
            r.hincrby(f"batch:{batch_id}", "running", 1)
            r.rpush(f"batch:{batch_id}:jobs", job["job_id"])
            set_job_progress(job["job_id"], "queued", 0)
            background_tasks.add_task(
                run_workflow_sync,
                job["job_id"],
                job["url"],
                job.get("brand_name"),
                job.get("extra_instructions"),
                job.get("priority"),
                batch_id,
            )
    else:
        # Dispatch batch processing task
        process_bulk_batch.apply_async(
            kwargs={"batch_id": batch_id, "jobs": jobs},
            queue="normal",
        )

    return BulkUploadResponse(
        batch_id=batch_id,
        total_jobs=len(jobs),
        job_ids=[j["job_id"] for j in jobs],
        status="queued",
        errors=errors,
    )


@app.get("/api/bulk/{batch_id}", response_model=BulkStatusResponse)
async def get_batch_status(batch_id: str):
    """Get the status of all jobs in a bulk batch."""
    import redis
    from api.celery_app import REDIS_URL

    r = redis.from_url(REDIS_URL, decode_responses=True)
    batch_meta = r.hgetall(f"batch:{batch_id}")

    if not batch_meta:
        raise HTTPException(status_code=404, detail=f"Batch {batch_id} not found")

    # Get individual job statuses
    job_ids = r.lrange(f"batch:{batch_id}:jobs", 0, -1)
    job_statuses = []
    for job_id in job_ids[:50]:  # Cap at 50 for response size
        result = celery_app.AsyncResult(job_id)
        progress = get_job_progress(job_id)
        job_statuses.append(JobStatusResponse(
            job_id=job_id,
            status=_celery_state_to_job_status(result.state),
            progress=int(progress.get("progress", 0)),
            current_step=progress.get("step", ""),
        ))

    return BulkStatusResponse(
        batch_id=batch_id,
        total=int(batch_meta.get("total", 0)),
        completed=int(batch_meta.get("completed", 0)),
        failed=int(batch_meta.get("failed", 0)),
        running=int(batch_meta.get("running", 0)),
        pending=int(batch_meta.get("pending", 0)),
        jobs=job_statuses,
    )


def _celery_state_to_job_status(celery_state: str) -> JobStatus:
    mapping = {
        "PENDING": JobStatus.PENDING,
        "STARTED": JobStatus.RUNNING,
        "PROGRESS": JobStatus.RUNNING,
        "SUCCESS": JobStatus.COMPLETED,
        "FAILURE": JobStatus.FAILED,
        "RETRY": JobStatus.RETRYING,
    }
    return mapping.get(celery_state, JobStatus.PENDING)


# ─── Demo endpoint (no queue, synchronous, for testing) ───────────────────────

@app.post("/api/demo")
async def demo_generate(request: GenerateRequest):
    """
    Run workflow synchronously (for demo/testing without Redis/Celery).
    WARNING: Will time out in production — use /api/generate for real jobs.
    """
    from graph import run_workflow
    from utils.progress import set_progress_callback, clear_progress_callback

    def on_progress(_jid: str, step: str, pct: int):
        logger.info("demo_progress", step=step, progress=pct)

    set_progress_callback(on_progress)
    try:
        state = await run_workflow(request.url)
        return state.model_dump(mode="json")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        clear_progress_callback()