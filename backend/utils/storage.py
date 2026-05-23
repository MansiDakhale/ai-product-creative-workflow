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

    def _get_field(obj, key, default=None):
        if isinstance(obj, dict):
            return obj.get(key, default)
        return getattr(obj, key, default)

    generated_images = _get_field(state, "generated_images", []) or []
    generated_videos = _get_field(state, "generated_videos", []) or []

    image_urls = [img.url if hasattr(img, "url") else img for img in generated_images]
    video_urls = [vid.url if hasattr(vid, "url") else vid for vid in generated_videos]

    review_score = None
    review_report = _get_field(state, "review_report")
    if review_report and getattr(review_report, "image_reviews", None):
        scores = [r.overall_score for r in review_report.image_reviews + review_report.video_reviews]
        if scores:
            review_score = round(sum(scores) / len(scores), 3)

    def _serialize(obj):
        if hasattr(obj, "model_dump"):
            return obj.model_dump(mode="json")
        if isinstance(obj, dict):
            return {k: _serialize(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [_serialize(v) for v in obj]
        return obj

    payload = {
        "id": _get_field(state, "job_id"),
        "product_url": _get_field(state, "url"),
        "brand": (
            _get_field(_get_field(state, "product_data"), "brand")
            if _get_field(state, "product_data")
            else (_get_field(state, "brand_name") or "")
        ),
        "research_data": _serialize(_get_field(state, "product_data")) if _get_field(state, "product_data") else None,
        "marketing_strategy": _serialize(_get_field(state, "creative_strategy")) if _get_field(state, "creative_strategy") else None,
        "image_urls": image_urls,
        "video_urls": video_urls,
        "review_summary": review_report.summary if review_report else None,
        "review_score": review_score,
        "raw_state": _serialize(state) if isinstance(state, dict) else (state.model_dump(mode="json") if hasattr(state, "model_dump") else None),
    }

    try:
        supabase.table(table).upsert(payload).execute()
        logger.info("supabase_persisted", job_id=state.job_id, table=table)
    except Exception as e:
        logger.warning("supabase_persist_failed", error=str(e), table=table)
