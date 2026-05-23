"""
agents/image_generation.py
Agent 4: Image Generation Workflow

Generates 5 product marketing images using external AI image generation APIs.
Primary: Pollinations (no auth)
Fallback: Hugging Face Inference API (SDXL)
Fallback: Stability AI (if configured)
"""

from __future__ import annotations
import os
import time
import base64
import httpx
import urllib.parse
import asyncio
import structlog
from pathlib import Path
from PIL import Image
import io

from models.schemas import WorkflowState, GeneratedImage, JobStatus
from utils.blob_storage import upload_bytes

logger = structlog.get_logger()


OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "./outputs"))
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


# Pollinations (no auth)
POLLINATIONS_URL = "https://image.pollinations.ai/p"

# HuggingFace Inference API
HF_API_URL = "https://api-inference.huggingface.co/models/stabilityai/stable-diffusion-xl-base-1.0"
# Stability AI generation endpoint (used when `STABILITY_API_KEY` is set)
# This is a generic v1 generation endpoint; replace with a more specific
# engine path if you need a particular model (e.g. stable-diffusion-xl).
STABILITY_API_URL = os.getenv("STABILITY_API_URL", "https://api.stability.ai/v2beta/stable-image/generate/core")


async def run_image_generation(state: WorkflowState) -> WorkflowState:
    """
    Agent 4: Generate 5 product marketing images.
    Tries Pollinations first, then HuggingFace, then Stability AI.
    """
    if not state.generated_prompts:
        state.error = "Cannot generate images: no prompts available"
        state.status = JobStatus.FAILED
        return state

    logger.info("agent4_image_gen_start", job_id=state.job_id)

    job_output_dir = OUTPUT_DIR / state.job_id / "images"
    job_output_dir.mkdir(parents=True, exist_ok=True)

    stability_key = os.getenv("STABILITY_API_KEY")
    hf_token = os.getenv("HF_TOKEN")

    generated = []
    for img_prompt in state.generated_prompts.image_prompts:
        logger.info("generating_image", index=img_prompt.index, job_id=state.job_id)
        start = time.time()

        image_bytes = None

        # ── Try 1: Pollinations (no auth) ───────────────────────────────
        if not image_bytes:
            for attempt in range(3):
                try:
                    image_bytes = await _generate_pollinations(img_prompt)
                    model_used = "Pollinations"
                    break
                except Exception as e:
                    logger.warning(
                        "pollinations_failed",
                        index=img_prompt.index,
                        attempt=attempt + 1,
                        error=str(e),
                    )
                    if _is_payment_required_error(e):
                        logger.warning(
                            "pollinations_rate_limited",
                            index=img_prompt.index,
                            job_id=state.job_id,
                        )
                        image_bytes = _generate_mockup_image(state, img_prompt, img_prompt.index)
                        model_used = "mockup"
                        break

        # ── Try 2: HuggingFace Inference API (SDXL) ───────────────────────
        if hf_token and not image_bytes:
            try:
                image_bytes = await _generate_huggingface(img_prompt, hf_token)
                model_used = "SDXL (HuggingFace Inference API)"
            except Exception as e:
                logger.warning("hf_failed", index=img_prompt.index, error=str(e))

        # ── Try 3: Stability AI ──────────────────────────────
        if stability_key and not image_bytes:
            try:
                image_bytes = await _generate_stability(img_prompt, stability_key)
                model_used = "Stable Image Ultra (Stability AI)"
            except Exception as e:
                logger.warning("stability_ai_failed", index=img_prompt.index, error=str(e))

        # ── Final fallback: Placeholder image ─────────────────────────────
        if not image_bytes:
            try:
                model_used = "mockup"
                image_bytes = _generate_mockup_image(state, img_prompt, img_prompt.index)
            except Exception as e:
                logger.warning("mockup_failed", index=img_prompt.index, error=str(e))
                raise

        # Save image
        elapsed_ms = int((time.time() - start) * 1000)
        file_name = f"image_{img_prompt.index:02d}.png"
        file_path = job_output_dir / file_name

        img = Image.open(io.BytesIO(image_bytes))
        img.save(file_path, format="PNG")

        public_url = None
        try:
            public_url = await upload_bytes(
                image_bytes,
                f"{state.job_id}/images/{file_name}",
                "image/png",
            )
        except Exception as e:
            logger.warning("blob_upload_failed", index=img_prompt.index, error=str(e))

        generated.append(GeneratedImage(
            index=img_prompt.index,
            file_path=str(file_path),
            url=public_url or f"/outputs/{state.job_id}/images/{file_name}",
            prompt_used=img_prompt.prompt,
            model=model_used,
            width=img.width,
            height=img.height,
            generation_time_ms=elapsed_ms,
        ))

        logger.info("image_generated", index=img_prompt.index, model=model_used, ms=elapsed_ms)

        # Rate limit courtesy sleep
        await asyncio.sleep(0.5)

    state.generated_images = generated
    logger.info("agent4_complete", job_id=state.job_id, count=len(generated))
    return state


async def _generate_stability(img_prompt, api_key: str) -> bytes:
    """Generate image using Stability AI API."""

    from utils.http_client import make_async_client

    async with make_async_client(timeout=180) as client:

        response = await client.post(
            STABILITY_API_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json",
            },
            files={
                "none": ("", b"")
            },
            data={
                "prompt": img_prompt.prompt,
                "output_format": "png",
            },
        )

        response.raise_for_status()

        data = response.json()

        image_bytes = base64.b64decode(data["image"])

        return image_bytes


async def _generate_huggingface(img_prompt, hf_token: str) -> bytes:
    """Generate image using HuggingFace Inference API."""
    from utils.http_client import make_async_client
    async with make_async_client(timeout=180) as client:
        resp = await client.post(
            HF_API_URL,
            headers={"Authorization": f"Bearer {hf_token}"},
            json={
                "inputs": img_prompt.prompt,
                "parameters": {
                    "negative_prompt": img_prompt.negative_prompt,
                    "num_inference_steps": 25,
                    "guidance_scale": 7.5,
                },
            },
        )
        resp.raise_for_status()
        return resp.content


async def _generate_pollinations(img_prompt) -> bytes:
    """Generate image using Pollinations (no auth required)."""
    from utils.http_client import make_async_client

    encoded_prompt = urllib.parse.quote(img_prompt.prompt)
    url = f"{POLLINATIONS_URL}/{encoded_prompt}?width=1024&height=1024&seed=42"
    async with make_async_client(timeout=60) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.content


def _is_payment_required_error(error: Exception) -> bool:
    """Detect Pollinations-style throttling so we can skip noisy retries."""
    if isinstance(error, httpx.HTTPStatusError) and error.response is not None:
        return error.response.status_code == 402
    message = str(error).lower()
    return "402" in message or "payment required" in message


def _generate_mockup_image(state: WorkflowState, img_prompt, index: int) -> bytes:
    """Generate a polished static mockup that looks deliberate rather than broken."""
    from PIL import ImageDraw, ImageFont

    width, height = _parse_aspect_ratio(getattr(img_prompt, "aspect_ratio", "1:1"))
    img = Image.new("RGB", (width, height), color=(13, 18, 33))
    draw = ImageDraw.Draw(img)

    for y in range(height):
        mix = y / max(height - 1, 1)
        r = int(14 + mix * 32)
        g = int(18 + mix * 44)
        b = int(33 + mix * 36)
        draw.line([(0, y), (width, y)], fill=(r, g, b))

    accent = (246, 176, 84)
    highlight = (245, 248, 255)
    muted = (180, 191, 210)

    panel_margin = int(width * 0.08)
    panel = [panel_margin, panel_margin, width - panel_margin, height - panel_margin]
    draw.rounded_rectangle(panel, radius=40, outline=(92, 107, 136), width=3, fill=(20, 27, 45))

    draw.ellipse((panel_margin + 30, panel_margin + 28, panel_margin + 150, panel_margin + 148), fill=(42, 54, 82))
    draw.ellipse((width - panel_margin - 170, panel_margin + 46, width - panel_margin - 58, panel_margin + 158), fill=(32, 49, 72))

    # Product spotlight pedestal
    cx = width // 2
    cy = int(height * 0.58)
    draw.ellipse((cx - 180, cy - 34, cx + 180, cy + 86), fill=(36, 46, 72))
    draw.ellipse((cx - 150, cy - 70, cx + 150, cy + 58), fill=(50, 64, 96))
    draw.ellipse((cx - 120, cy - 100, cx + 120, cy + 32), fill=(72, 89, 132))

    # Abstract product silhouette
    draw.rounded_rectangle((cx - 110, cy - 250, cx + 110, cy - 40), radius=36, fill=(238, 241, 247))
    draw.rounded_rectangle((cx - 78, cy - 212, cx + 78, cy - 80), radius=28, fill=(255, 255, 255))
    draw.rectangle((cx - 40, cy - 285, cx + 40, cy - 235), fill=(210, 217, 228))

    # Decorative rings and badges
    for radius, outline_color in ((220, (79, 97, 135)), (260, (48, 70, 111))):
        draw.arc((cx - radius, cy - radius - 150, cx + radius, cy + radius - 150), 200, 340, fill=outline_color, width=8)

    badge_box = (panel_margin + 34, height - panel_margin - 180, panel_margin + 260, height - panel_margin - 88)
    draw.rounded_rectangle(badge_box, radius=24, fill=(33, 47, 74), outline=(89, 107, 142), width=2)

    # Text blocks
    title = (state.brand_name or getattr(state.product_data, "brand", "Premium")).strip() or "Premium"
    headline = getattr(state.product_data, "title", "Product Spotlight")
    tagline = getattr(state.creative_strategy, "primary_hook", "High-impact product story") if state.creative_strategy else "High-impact product story"
    subtitle = getattr(state.product_data, "usp", "Crafted for a clean, editorial product demo") or "Crafted for a clean, editorial product demo"

    def _fit_text(text: str, limit: int) -> str:
        text = " ".join(text.split())
        return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"

    draw.text((panel_margin + 40, panel_margin + 42), _fit_text(title.upper(), 22), fill=accent, anchor="la")
    draw.text((panel_margin + 40, panel_margin + 96), _fit_text(headline, 38), fill=highlight, anchor="la")
    draw.text((panel_margin + 40, panel_margin + 152), _fit_text(tagline, 52), fill=muted, anchor="la")
    draw.text((panel_margin + 40, panel_margin + 202), _fit_text(subtitle, 62), fill=(205, 213, 228), anchor="la")
    draw.text((panel_margin + 56, height - panel_margin - 150), f"IMAGE {index:02d}", fill=highlight, anchor="la")
    draw.text((panel_margin + 56, height - panel_margin - 118), "Demo-ready fallback mockup", fill=muted, anchor="la")

    prompt_preview = _fit_text(img_prompt.prompt, 110)
    draw.text((panel_margin + 34, panel_margin + 242), prompt_preview, fill=(214, 221, 235), anchor="la")

    # Footer bar
    footer_y = height - panel_margin - 52
    draw.rounded_rectangle((panel_margin + 36, footer_y, width - panel_margin - 36, footer_y + 18), radius=9, fill=accent)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _parse_aspect_ratio(ratio: str) -> tuple[int, int]:
    """Convert aspect ratio string to pixel dimensions."""
    ratios = {
        "1:1": (1024, 1024),
        "16:9": (1344, 768),
        "9:16": (768, 1344),
        "4:3": (1152, 896),
        "3:4": (896, 1152),
    }
    return ratios.get(ratio, (1024, 1024))