"""
utils/storage.py
Persist workflow artifacts (JSON reports + metadata) per job.
"""

from __future__ import annotations
import json
import os
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger()

OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "./outputs"))


def job_dir(job_id: str) -> Path:
    d = OUTPUT_DIR / job_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_json(job_id: str, filename: str, data: Any) -> Path:
    """Write a JSON artifact under outputs/{job_id}/."""
    path = job_dir(job_id) / filename
    payload = data
    if hasattr(data, "model_dump"):
        payload = data.model_dump(mode="json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, default=str)
    logger.debug("artifact_saved", job_id=job_id, file=filename)
    return path


def save_workflow_artifacts(state) -> None:
    """Persist intermediate/final JSON outputs for a completed workflow step."""
    jid = state.job_id
    if state.product_data:
        save_json(jid, "product_research.json", state.product_data)
    if state.creative_strategy:
        save_json(jid, "creative_strategy.json", state.creative_strategy)
    if state.generated_prompts:
        save_json(jid, "prompts.json", state.generated_prompts)
    if state.review_report:
        save_json(jid, "review_report.json", state.review_report)


def persist_workflow_state(state) -> None:
    """Persist workflow metadata and asset URLs to Supabase when configured."""
    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    if not supabase_url or not supabase_key:
        logger.info("supabase_not_configured")
        return

    try:
        from supabase import create_client
    except Exception as e:
        logger.warning("supabase_import_failed", error=str(e))
        return

    table = os.getenv("SUPABASE_TABLE", "campaign_workflows")
    supabase = create_client(supabase_url, supabase_key)

    image_urls = [img.url for img in (state.generated_images or [])]
    video_urls = [vid.url for vid in (state.generated_videos or [])]

    review_score = None
    if state.review_report and state.review_report.image_reviews:
        scores = [r.overall_score for r in state.review_report.image_reviews + state.review_report.video_reviews]
        if scores:
            review_score = round(sum(scores) / len(scores), 3)

    payload = {
        "id": state.job_id,
        "product_url": state.url,
        "brand": state.product_data.brand if state.product_data else (state.brand_name or ""),
        "research_data": state.product_data.model_dump(mode="json") if state.product_data else None,
        "marketing_strategy": state.creative_strategy.model_dump(mode="json") if state.creative_strategy else None,
        "image_urls": image_urls,
        "video_urls": video_urls,
        "review_summary": state.review_report.summary if state.review_report else None,
        "review_score": review_score,
        "raw_state": state.model_dump(mode="json") if hasattr(state, "model_dump") else None,
    }

    try:
        supabase.table(table).upsert(payload).execute()
        logger.info("supabase_persisted", job_id=state.job_id, table=table)
    except Exception as e:
        logger.warning("supabase_persist_failed", error=str(e), table=table)
