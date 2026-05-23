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
            try:
                image_bytes = await _generate_pollinations(img_prompt)
                model_used = "Pollinations"
            except Exception as e:
                logger.warning("pollinations_failed", index=img_prompt.index, error=str(e))

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
                model_used = "placeholder"
                image_bytes = _generate_placeholder_image(img_prompt, img_prompt.index)
            except Exception as e:
                logger.warning("placeholder_failed", index=img_prompt.index, error=str(e))
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


def _generate_placeholder_image(img_prompt, index: int) -> bytes:
    """Last resort: generate a labeled placeholder image."""
    from PIL import ImageDraw, ImageFont
    img = Image.new("RGB", (1024, 1024), color=(30, 30, 50))
    draw = ImageDraw.Draw(img)
    
    # Draw gradient-like header
    for y in range(200):
        r = int(30 + (y / 200) * 50)
        draw.line([(0, y), (1024, y)], fill=(r, 30, 80))

    draw.text((512, 100), f"IMAGE {index}", fill=(255, 255, 255), anchor="mm")
    draw.text((512, 200), "AI Generation Placeholder", fill=(200, 200, 200), anchor="mm")

    # Wrap prompt text
    words = img_prompt.prompt.split()
    lines, line = [], []
    for w in words:
        line.append(w)
        if len(" ".join(line)) > 60:
            lines.append(" ".join(line[:-1]))
            line = [w]
    if line:
        lines.append(" ".join(line))

    for i, ln in enumerate(lines[:6]):
        draw.text((512, 350 + i * 40), ln, fill=(180, 180, 180), anchor="mm")

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