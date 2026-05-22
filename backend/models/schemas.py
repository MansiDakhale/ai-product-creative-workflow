"""
models/schemas.py
Pydantic models for the entire workflow pipeline.
"""

from __future__ import annotations
from typing import Any, Optional
from pydantic import BaseModel, HttpUrl, Field
from enum import Enum
import uuid
from datetime import datetime


# ─── Enums ────────────────────────────────────────────────────────────────────

class JobStatus(str, Enum):
    PENDING   = "pending"
    RUNNING   = "running"
    COMPLETED = "completed"
    FAILED    = "failed"
    RETRYING  = "retrying"


class Priority(str, Enum):
    HIGH   = "high"
    NORMAL = "normal"
    LOW    = "low"


# ─── Product Research ─────────────────────────────────────────────────────────

class ProductReview(BaseModel):
    rating: float
    summary: str
    count: int = 0


class ProductData(BaseModel):
    title: str
    brand: str = ""
    description: str
    features: list[str] = []
    specifications: dict[str, str] = {}
    price: str = ""
    currency: str = "USD"
    category: str = ""
    reviews: Optional[ProductReview] = None
    target_audience: str = ""
    usp: str = ""          # Unique Selling Proposition
    raw_html_snippet: str = ""
    source_url: str


# ─── Creative Strategy ────────────────────────────────────────────────────────

class AudienceAngle(BaseModel):
    segment: str           # e.g. "fitness enthusiasts aged 25-35"
    pain_point: str
    hook: str
    cta: str


class VisualTheme(BaseModel):
    name: str              # e.g. "bold lifestyle", "minimalist product"
    color_palette: list[str]
    mood: str
    composition_notes: str


class CreativeStrategy(BaseModel):
    primary_hook: str
    secondary_hooks: list[str] = []
    audience_angles: list[AudienceAngle] = []
    visual_themes: list[VisualTheme] = []
    caption_ideas: list[str] = []
    hashtag_suggestions: list[str] = []
    platform_notes: dict[str, str] = {}   # {"instagram": "...", "tiktok": "..."}


# ─── Prompt Generation ────────────────────────────────────────────────────────

class ImagePrompt(BaseModel):
    index: int
    prompt: str
    negative_prompt: str = ""
    style_tags: list[str] = []
    theme_ref: str = ""    # which VisualTheme this maps to
    aspect_ratio: str = "1:1"


class VideoPrompt(BaseModel):
    index: int
    prompt: str
    duration_seconds: int = 6
    motion_style: str = ""   # e.g. "slow zoom", "product reveal"
    audio_notes: str = ""


class GeneratedPrompts(BaseModel):
    image_prompts: list[ImagePrompt]
    video_prompts: list[VideoPrompt]


# ─── Generated Assets ────────────────────────────────────────────────────────

class GeneratedImage(BaseModel):
    index: int
    file_path: str
    url: str = ""
    prompt_used: str
    model: str
    width: int = 1024
    height: int = 1024
    generation_time_ms: int = 0


class GeneratedVideo(BaseModel):
    index: int
    file_path: str
    url: str = ""
    prompt_used: str
    model: str
    duration_seconds: float
    generation_time_ms: int = 0


# ─── Review / Critic ─────────────────────────────────────────────────────────

class AssetReview(BaseModel):
    asset_type: str        # "image" or "video"
    asset_index: int
    brand_consistency: float   # 0-1
    product_accuracy: float
    visual_quality: float
    hook_strength: float
    cta_clarity: float
    overall_score: float
    issues: list[str] = []
    passed: bool
    revised_prompt: Optional[str] = None


class ReviewReport(BaseModel):
    image_reviews: list[AssetReview] = []
    video_reviews: list[AssetReview] = []
    overall_passed: bool
    retry_recommended: bool
    summary: str


# ─── Workflow State (LangGraph) ───────────────────────────────────────────────

class WorkflowState(BaseModel):
    """
    Shared state passed between all LangGraph agent nodes.
    Each agent reads + writes relevant fields.
    """
    job_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    url: str
    status: JobStatus = JobStatus.PENDING
    retry_count: int = 0
    max_retries: int = 2
    error: Optional[str] = None
    brand_name: Optional[str] = None
    extra_instructions: Optional[str] = None
    priority: Priority = Priority.NORMAL

    # Agent outputs (populated as workflow progresses)
    product_data: Optional[ProductData] = None
    creative_strategy: Optional[CreativeStrategy] = None
    generated_prompts: Optional[GeneratedPrompts] = None
    generated_images: list[GeneratedImage] = []
    generated_videos: list[GeneratedVideo] = []
    review_report: Optional[ReviewReport] = None

    # Metadata
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    total_duration_seconds: Optional[float] = None

    class Config:
        use_enum_values = True


# ─── API Request/Response ─────────────────────────────────────────────────────

class GenerateRequest(BaseModel):
    url: str
    priority: Priority = Priority.NORMAL
    brand_name: Optional[str] = None
    extra_instructions: Optional[str] = None


class JobStatusResponse(BaseModel):
    job_id: str
    status: JobStatus
    progress: int = 0          # 0-100
    current_step: str = ""
    error: Optional[str] = None
    result: Optional[dict[str, Any]] = None


class BulkUploadResponse(BaseModel):
    batch_id: str
    total_jobs: int
    job_ids: list[str]
    status: str = "queued"
    errors: list[str] = []


class BulkStatusResponse(BaseModel):
    batch_id: str
    total: int
    completed: int
    failed: int
    running: int
    pending: int
    jobs: list[JobStatusResponse] = []


# ─── CSV Row ─────────────────────────────────────────────────────────────────

class CSVRow(BaseModel):
    url: str
    brand_name: Optional[str] = None
    priority: Priority = Priority.NORMAL
    extra_instructions: Optional[str] = None