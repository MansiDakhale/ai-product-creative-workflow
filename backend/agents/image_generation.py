"""
agents/image_generation.py
Agent 4: Image Generation Workflow

Generates 5 product marketing images using AI image generation models.
Primary: Together AI FLUX.1-schnell (free tier)
Fallback: Hugging Face Inference API (SDXL)
Local fallback: diffusers + SDXL-Turbo (CPU-compatible)
"""

from __future__ import annotations
import os
import time
import base64
import httpx
import asyncio
import structlog
from pathlib import Path
from PIL import Image
import io

from models.schemas import WorkflowState, GeneratedImage, JobStatus

logger = structlog.get_logger()

OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "./outputs"))
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Together AI FLUX endpoint
TOGETHER_API_URL = "https://api.together.xyz/v1/images/generations"
# HuggingFace Inference API
HF_API_URL = "https://api-inference.huggingface.co/models/stabilityai/stable-diffusion-xl-base-1.0"


async def run_image_generation(state: WorkflowState) -> WorkflowState:
    """
    Agent 4: Generate 5 product marketing images.
    Tries Together AI first, falls back to HuggingFace, then local diffusers.
    """
    if not state.generated_prompts:
        state.error = "Cannot generate images: no prompts available"
        state.status = JobStatus.FAILED
        return state

    logger.info("agent4_image_gen_start", job_id=state.job_id)

    job_output_dir = OUTPUT_DIR / state.job_id / "images"
    job_output_dir.mkdir(parents=True, exist_ok=True)

    together_key = os.getenv("TOGETHER_API_KEY")
    hf_token = os.getenv("HF_TOKEN")

    generated = []
    for img_prompt in state.generated_prompts.image_prompts:
        logger.info("generating_image", index=img_prompt.index, job_id=state.job_id)
        start = time.time()

        image_bytes = None

        # ── Try 1: Together AI FLUX.1-schnell ──────────────────────────────
        if together_key and not image_bytes:
            try:
                image_bytes = await _generate_together(img_prompt, together_key)
                model_used = "FLUX.1-schnell (Together AI)"
            except Exception as e:
                # Detect rate limit (HTTP 429) when possible
                msg = str(e)
                if hasattr(e, "response") and getattr(e.response, "status_code", None) == 429:
                    logger.error("agent4_rate_limited", job_id=state.job_id, index=img_prompt.index, error=msg)
                else:
                    logger.warning("together_ai_failed", index=img_prompt.index, error=msg)

        # ── Try 2: HuggingFace Inference API (SDXL) ───────────────────────
        if hf_token and not image_bytes:
            try:
                image_bytes = await _generate_huggingface(img_prompt, hf_token)
                model_used = "SDXL (HuggingFace Inference API)"
            except Exception as e:
                msg = str(e)
                # httpx raises HTTPStatusError with response attribute
                if hasattr(e, "response") and getattr(e.response, "status_code", None) == 429:
                    logger.error("agent4_rate_limited", job_id=state.job_id, index=img_prompt.index, error=msg)
                else:
                    logger.warning("hf_failed", index=img_prompt.index, error=msg)

        # ── Try 3: Local diffusers (CPU) ───────────────────────────────────
        if not image_bytes:
            try:
                image_bytes = await _generate_local_diffusers(img_prompt)
                model_used = "SDXL-Turbo (local diffusers)"
            except Exception as e:
                logger.warning("local_diffusers_failed", index=img_prompt.index, error=str(e))
                model_used = "placeholder"
                image_bytes = _generate_placeholder_image(img_prompt, img_prompt.index)

        # Save image
        elapsed_ms = int((time.time() - start) * 1000)
        file_name = f"image_{img_prompt.index:02d}.png"
        file_path = job_output_dir / file_name

        img = Image.open(io.BytesIO(image_bytes))
        img.save(file_path, format="PNG")

        generated.append(GeneratedImage(
            index=img_prompt.index,
            file_path=str(file_path),
            url=f"/outputs/{state.job_id}/images/{file_name}",
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


async def _generate_together(img_prompt, api_key: str) -> bytes:
    """Generate image using Together AI FLUX.1-schnell."""
    width, height = _parse_aspect_ratio(img_prompt.aspect_ratio)
    
    from utils.http_client import make_async_client
    async with make_async_client(timeout=120) as client:
        resp = await client.post(
            TOGETHER_API_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": "black-forest-labs/FLUX.1-schnell-Free",
                "prompt": img_prompt.prompt,
                "width": width,
                "height": height,
                "steps": 4,
                "n": 1,
                "response_format": "b64_json",
            },
        )
        resp.raise_for_status()
        data = resp.json()
        b64_data = data["data"][0]["b64_json"]
        return base64.b64decode(b64_data)


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


async def _generate_local_diffusers(img_prompt) -> bytes:
    """Generate image using local diffusers pipeline (CPU fallback)."""
    # Run in executor to avoid blocking event loop
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _diffusers_sync, img_prompt)


def _diffusers_sync(img_prompt) -> bytes:
    from diffusers import StableDiffusionXLPipeline, DPMSolverMultistepScheduler
    import torch

    device = "cuda" if torch.cuda.is_available() else "cpu"
    dtype = torch.float16 if device == "cuda" else torch.float32

    pipe = StableDiffusionXLPipeline.from_pretrained(
        "stabilityai/sdxl-turbo",
        torch_dtype=dtype,
        variant="fp16" if device == "cuda" else None,
    )
    pipe = pipe.to(device)
    pipe.scheduler = DPMSolverMultistepScheduler.from_config(pipe.scheduler.config)

    image = pipe(
        prompt=img_prompt.prompt,
        negative_prompt=img_prompt.negative_prompt,
        num_inference_steps=4 if device == "cpu" else 20,
        guidance_scale=0.0 if "turbo" in "sdxl-turbo" else 7.5,
    ).images[0]

    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


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