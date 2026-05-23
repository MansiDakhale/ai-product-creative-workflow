"""
agents/prompt_generation.py
Agent 3: Prompt Generation Agent

Creates highly optimized prompts for:
- 5 product marketing images (FLUX.1 / SDXL compatible)
- 2 short product videos (CogVideoX / AnimateDiff compatible)
"""

from __future__ import annotations
import json
import os
import structlog
from tenacity import retry, stop_after_attempt, wait_exponential

from models.schemas import (
    WorkflowState, GeneratedPrompts,
    ImagePrompt, VideoPrompt, JobStatus
)

logger = structlog.get_logger()

IMAGE_PROMPT_SYSTEM = """You are an expert AI image generation prompt engineer.
You specialize in writing prompts for FLUX.1, Stable Diffusion XL, and Midjourney
for ecommerce product marketing images.

Your prompts should be:
1. Highly detailed and specific (not generic)
2. Include lighting, composition, camera angle, mood
3. Reference specific visual styles when appropriate
4. Include negative prompts to avoid common issues
5. Optimized for commercial/marketing use

Return ONLY a JSON object with an "image_prompts" array of 5 objects:
{
  "image_prompts": [
    {
      "index": 1,
      "prompt": "detailed positive prompt here",
      "negative_prompt": "blurry, low quality, watermark, text, deformed",
      "style_tags": ["photorealistic", "studio lighting"],
      "theme_ref": "which visual theme this uses",
      "aspect_ratio": "1:1"
    }
  ]
}

Vary aspects across the 5 images:
- Image 1: Hero product shot (white/neutral background, studio lighting)
- Image 2: Lifestyle shot (product in use, real environment)
- Image 3: Detail/texture close-up shot
- Image 4: Aspirational/emotional scene
- Image 5: Comparison or benefit visualization

Return ONLY valid JSON."""

VIDEO_PROMPT_SYSTEM = """You are an expert AI video generation prompt engineer.
You write prompts for CogVideoX and AnimateDiff models for short marketing videos.

Video prompts should describe:
- What is shown in the video (subject, setting, action)
- Camera motion (pan, zoom, static, dolly)
- The visual narrative arc (beginning → middle → end)
- Mood and energy level
- Lighting and color

Return ONLY a JSON object with a "video_prompts" array of 2 objects:
{
  "video_prompts": [
    {
      "index": 1,
      "prompt": "detailed video prompt describing the full 6-second clip",
      "duration_seconds": 6,
      "motion_style": "slow zoom / product reveal / lifestyle montage / etc.",
      "audio_notes": "upbeat background music / voiceover / etc."
    }
  ]
}

Video 1: Product reveal / hero video (product-focused, high production value)
Video 2: Lifestyle/use-case video (person using the product, emotional connection)

Return ONLY valid JSON."""


def build_image_prompt_request(product_data, creative_strategy, extra_instructions: str | None = None) -> str:
    themes = [
        f"Theme '{t.name}': {t.mood}, colors: {', '.join(t.color_palette)}, "
        f"composition: {t.composition_notes}"
        for t in (creative_strategy.visual_themes or [])
    ]
    themes_text = "\n".join(themes) if themes else "No specific themes defined"

    extra = f"\nEXTRA INSTRUCTIONS: {extra_instructions}" if extra_instructions else ""

    return f"""Generate 5 optimized image generation prompts for this product:

PRODUCT: {product_data.title}
BRAND: {product_data.brand}
DESCRIPTION: {product_data.description[:400]}
KEY FEATURES: {', '.join(product_data.features[:5])}
USP: {product_data.usp}
PRIMARY HOOK: {creative_strategy.primary_hook}

VISUAL THEMES TO USE:
{themes_text}
{extra}
Create professional product marketing images that look like they were shot by
a high-end commercial photographer for a premium brand campaign."""


def build_video_prompt_request(product_data, creative_strategy, extra_instructions: str | None = None) -> str:
    hooks = [creative_strategy.primary_hook] + creative_strategy.secondary_hooks[:2]
    hooks_text = "\n".join(f"• {h}" for h in hooks)

    extra = f"\nEXTRA INSTRUCTIONS: {extra_instructions}" if extra_instructions else ""

    return f"""Generate 2 optimized video generation prompts for this product:

PRODUCT: {product_data.title}
BRAND: {product_data.brand}
DESCRIPTION: {product_data.description[:400]}
TARGET AUDIENCE: {product_data.target_audience}
USP: {product_data.usp}

MARKETING HOOKS:
{hooks_text}
{extra}
Create compelling short-form video content (6 seconds each) that would perform
well on Instagram Reels, TikTok, and YouTube Shorts. The videos should feel
premium and stop-scroll-worthy."""


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
async def run_prompt_generation(state: WorkflowState, groq_client) -> WorkflowState:
    """
    Agent 3: Generate optimized prompts for image and video generation models.
    """
    if not state.product_data or not state.creative_strategy:
        state.error = "Cannot generate prompts: missing product_data or creative_strategy"
        state.status = JobStatus.FAILED
        return state

    logger.info("agent3_prompt_generation_start", job_id=state.job_id)

    # ── Generate image prompts ──────────────────────────────────────────────
    try:
        model = os.getenv("GROQ_MODEL", "llama-3.3-70b-specdec")
        fallback_model = os.getenv("GROQ_FALLBACK_MODEL")

        try:
            img_response = groq_client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": IMAGE_PROMPT_SYSTEM},
                    {"role": "user", "content": build_image_prompt_request(
                        state.product_data, state.creative_strategy, state.extra_instructions
                    )},
                ],
                temperature=0.7,
                max_tokens=3000,
                response_format={"type": "json_object"},
            )
        except Exception as e:
            if ("ratelimit" in str(e).lower() or "rate limit" in str(e).lower() or "RateLimitError" in e.__class__.__name__) and fallback_model:
                img_response = groq_client.chat.completions.create(
                    model=fallback_model,
                    messages=[
                        {"role": "system", "content": IMAGE_PROMPT_SYSTEM},
                        {"role": "user", "content": build_image_prompt_request(
                            state.product_data, state.creative_strategy, state.extra_instructions
                        )},
                    ],
                    temperature=0.7,
                    max_tokens=3000,
                    response_format={"type": "json_object"},
                )
            else:
                raise

        img_dict = json.loads(img_response.choices[0].message.content)
        image_prompts = [ImagePrompt(**p) for p in img_dict.get("image_prompts", [])]
    except Exception as e:
        logger.error("image_prompt_gen_failed", error=str(e))
        image_prompts = _fallback_image_prompts(state.product_data)

    # ── Generate video prompts ──────────────────────────────────────────────
    try:
        try:
            vid_response = groq_client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": VIDEO_PROMPT_SYSTEM},
                    {"role": "user", "content": build_video_prompt_request(
                        state.product_data, state.creative_strategy, state.extra_instructions
                    )},
                ],
                temperature=0.7,
                max_tokens=1500,
                response_format={"type": "json_object"},
            )
        except Exception as e:
            if ("ratelimit" in str(e).lower() or "rate limit" in str(e).lower() or "RateLimitError" in e.__class__.__name__) and fallback_model:
                vid_response = groq_client.chat.completions.create(
                    model=fallback_model,
                    messages=[
                        {"role": "system", "content": VIDEO_PROMPT_SYSTEM},
                        {"role": "user", "content": build_video_prompt_request(
                            state.product_data, state.creative_strategy, state.extra_instructions
                        )},
                    ],
                    temperature=0.7,
                    max_tokens=1500,
                    response_format={"type": "json_object"},
                )
            else:
                raise

        vid_dict = json.loads(vid_response.choices[0].message.content)
        video_prompts = [VideoPrompt(**p) for p in vid_dict.get("video_prompts", [])]
    except Exception as e:
        logger.error("video_prompt_gen_failed", error=str(e))
        video_prompts = _fallback_video_prompts(state.product_data)

    state.generated_prompts = GeneratedPrompts(
        image_prompts=image_prompts,
        video_prompts=video_prompts,
    )

    logger.info(
        "agent3_complete",
        job_id=state.job_id,
        image_count=len(image_prompts),
        video_count=len(video_prompts),
    )
    return state


# ── Fallback prompts (if LLM fails) ──────────────────────────────────────────

def _fallback_image_prompts(product_data) -> list[ImagePrompt]:
    name = product_data.title
    return [
        ImagePrompt(
            index=i + 1,
            prompt=(
                f"Professional product photography of {name}, "
                f"{'studio white background, soft lighting' if i == 0 else 'lifestyle setting'}, "
                f"commercial marketing quality, sharp focus, 8k"
            ),
            negative_prompt="blurry, low quality, watermark, text, deformed, ugly",
            style_tags=["photorealistic", "commercial photography"],
            aspect_ratio="1:1",
        )
        for i in range(5)
    ]


def _fallback_video_prompts(product_data) -> list[VideoPrompt]:
    name = product_data.title
    return [
        VideoPrompt(
            index=1,
            prompt=(
                f"Cinematic product reveal of {name}, slow zoom in, "
                f"studio lighting, dark background with dramatic highlights, "
                f"premium commercial look"
            ),
            duration_seconds=6,
            motion_style="slow zoom",
        ),
        VideoPrompt(
            index=2,
            prompt=(
                f"Lifestyle video showing someone using {name} in a modern home setting, "
                f"natural lighting, camera pans from environment to product close-up, "
                f"warm and inviting atmosphere"
            ),
            duration_seconds=6,
            motion_style="pan and focus",
        ),
    ]