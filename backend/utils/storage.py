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
