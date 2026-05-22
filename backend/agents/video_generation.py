"""
agents/video_generation.py
Agent 5: Video Generation Workflow

Generates 2 short product marketing videos.
Primary:   Fal.ai fast-animation
Fallback1: Replicate AnimateDiff
Fallback2: Replicate CogVideoX-5b
Fallback3: PIL-based slideshow from generated images (always works)
"""

from __future__ import annotations
import os
import time
import asyncio
import httpx
import structlog
from pathlib import Path

from models.schemas import WorkflowState, GeneratedVideo, JobStatus

logger = structlog.get_logger()

OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "./outputs"))


async def run_video_generation(state: WorkflowState) -> WorkflowState:
    """
    Agent 5: Generate 2 short marketing videos.
    """
    if not state.generated_prompts:
        state.error = "Cannot generate videos: no prompts available"
        state.status = JobStatus.FAILED
        return state

    logger.info("agent5_video_gen_start", job_id=state.job_id)

    job_output_dir = OUTPUT_DIR / state.job_id / "videos"
    job_output_dir.mkdir(parents=True, exist_ok=True)

    replicate_key = os.getenv("REPLICATE_API_KEY")
    fal_key = os.getenv("FAL_KEY")
    generated = []

    for vid_prompt in state.generated_prompts.video_prompts:
        logger.info("generating_video", index=vid_prompt.index, job_id=state.job_id)
        start = time.time()

        video_bytes = None
        model_used = ""

        # ── Try 1: Fal.ai Fast Animation ─────────────────────────────────
        if fal_key and not video_bytes:
            try:
                video_bytes = await _generate_fal_animation(state, vid_prompt, fal_key)
                model_used = "Fast Animation (Fal.ai)"
            except Exception as e:
                logger.warning("fal_animation_failed", index=vid_prompt.index, error=str(e))

        # ── Try 2: Replicate AnimateDiff-Lightning (reliable, low cost) ───
        if replicate_key and not video_bytes:
            try:
                video_bytes = await _generate_animatediff_replicate(vid_prompt, replicate_key)
                model_used = "AnimateDiff-Lightning (Replicate)"
            except Exception as e:
                logger.warning("animatediff_replicate_failed", index=vid_prompt.index, error=str(e))

        # ── Try 3: Replicate CogVideoX (optional; needs valid model version) ─
        if replicate_key and not video_bytes:
            try:
                video_bytes = await _generate_cogvideox(vid_prompt, replicate_key)
                model_used = "CogVideoX (Replicate)"
            except Exception as e:
                logger.warning("cogvideox_failed", index=vid_prompt.index, error=str(e))

        # ── Fallback: Slideshow from existing images ───────────────────────
        if not video_bytes:
            logger.info("using_slideshow_fallback", index=vid_prompt.index)
            try:
                video_bytes = await _generate_slideshow(state, vid_prompt, job_output_dir)
                model_used = "Image slideshow (imageio)"
            except ImportError as e:
                logger.warning("slideshow_fallback_failed", error=str(e))
                video_bytes = _generate_minimal_mp4(vid_prompt.duration_seconds)
                model_used = "Minimal placeholder video"

        elapsed_ms = int((time.time() - start) * 1000)
        file_name = f"video_{vid_prompt.index:02d}.mp4"
        file_path = job_output_dir / file_name

        with open(file_path, "wb") as f:
            f.write(video_bytes)

        generated.append(GeneratedVideo(
            index=vid_prompt.index,
            file_path=str(file_path),
            url=f"/outputs/{state.job_id}/videos/{file_name}",
            prompt_used=vid_prompt.prompt,
            model=model_used,
            duration_seconds=float(vid_prompt.duration_seconds),
            generation_time_ms=elapsed_ms,
        ))

        logger.info("video_generated", index=vid_prompt.index, model=model_used, ms=elapsed_ms)
        await asyncio.sleep(1.0)   # API courtesy delay

    state.generated_videos = generated
    logger.info("agent5_complete", job_id=state.job_id, count=len(generated))
    return state


# ── Generation backends ───────────────────────────────────────────────────────

async def _generate_cogvideox(vid_prompt, api_key: str) -> bytes:
    """Generate video using CogVideoX on Replicate (latest published version)."""
    import replicate

    os.environ["REPLICATE_API_TOKEN"] = api_key
    output = replicate.run(
        "thudm/cogvideox",
        input={
            "prompt": vid_prompt.prompt,
            "num_frames": min(49, 8 * vid_prompt.duration_seconds),
            "fps": 8,
            "guidance_scale": 6,
            "num_inference_steps": 50,
        },
    )
    video_url = output if isinstance(output, str) else str(output)
    from utils.http_client import make_async_client
    async with make_async_client(timeout=300) as client:
        resp = await client.get(video_url)
        resp.raise_for_status()
        return resp.content


async def _generate_animatediff_replicate(vid_prompt, api_key: str) -> bytes:
    """Generate video using AnimateDiff-Lightning on Replicate."""
    import replicate

    os.environ["REPLICATE_API_TOKEN"] = api_key
    output = replicate.run(
        "lucataco/animatediff-lightning-4step:727e49a643e999d602a896d774a7316d41b862b5f57b09f8943af4d50c31e",
        input={
            "prompt": vid_prompt.prompt,
            "negative_prompt": "blurry, low quality, watermark",
            "width": 512,
            "height": 512,
            "num_frames": 16,
            "guidance_scale": 1.5,
            "num_inference_steps": 4,
        },
    )
    from utils.http_client import make_async_client
    async with make_async_client(timeout=120) as client:
        resp = await client.get(str(output))
        resp.raise_for_status()
        return resp.content


async def _generate_fal_animation(state: WorkflowState, vid_prompt, api_key: str) -> bytes:
    """Generate video via Fal.ai Fast Animation and return bytes."""
    from utils.http_client import make_async_client

    image_url = _pick_reference_image_url(state)
    if not image_url:
        raise RuntimeError("No public image URL available for Fal.ai animation")

    async with make_async_client(timeout=240) as client:
        response = await client.post(
            "https://queue.fal.run/fal-ai/fast-animation",
            headers={
                "Authorization": f"Key {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "prompt": vid_prompt.prompt,
                "image_url": image_url,
                "sync_mode": True,
            },
        )
        response.raise_for_status()
        data = response.json()
        video_url = data.get("video", {}).get("url", "")
        if not video_url:
            raise RuntimeError("Fal.ai response missing video URL")

        video_resp = await client.get(video_url)
        video_resp.raise_for_status()
        return video_resp.content


def _pick_reference_image_url(state: WorkflowState) -> str:
    """Select a public image URL for video generation (Fal.ai requires it)."""
    for gen_img in state.generated_images:
        if gen_img.url.startswith("http"):
            return gen_img.url
    return ""


async def _generate_slideshow(state: WorkflowState, vid_prompt, output_dir: Path) -> bytes:
    """
    Fallback: create a video slideshow from generated images using imageio.
    This always works and produces a decent result.
    """
    import imageio
    import numpy as np
    from PIL import Image, ImageDraw
    import io as _io

    frames = []
    img_paths = [img.file_path for img in state.generated_images[:4]]

    fps = 8
    duration_per_image = 2  # seconds
    frames_per_image = fps * duration_per_image

    for img_path in img_paths:
        if not os.path.exists(img_path):
            continue
        base_img = Image.open(img_path).convert("RGB").resize((512, 512))
        base_arr = np.array(base_img)

        # Simple Ken Burns effect: gentle zoom over frames
        for i in range(frames_per_image):
            progress = i / frames_per_image
            scale = 1.0 + 0.05 * progress   # 0-5% zoom
            new_w = int(512 * scale)
            new_h = int(512 * scale)
            zoomed = base_img.resize((new_w, new_h), Image.LANCZOS)
            # Center crop back to 512x512
            x_off = (new_w - 512) // 2
            y_off = (new_h - 512) // 2
            cropped = zoomed.crop((x_off, y_off, x_off + 512, y_off + 512))
            frames.append(np.array(cropped))

    if not frames:
        # Generate a solid color placeholder with text
        img = Image.new("RGB", (512, 512), (30, 30, 50))
        draw = ImageDraw.Draw(img)
        draw.text((256, 256), "Product Video\n(Preview)", fill=(255, 255, 255), anchor="mm")
        for _ in range(fps * vid_prompt.duration_seconds):
            frames.append(np.array(img))

    # Write to mp4 using imageio
    tmp_path = output_dir / f"_tmp_slideshow_{vid_prompt.index}.mp4"
    writer = imageio.get_writer(
        str(tmp_path),
        fps=fps,
        codec="libx264",
        quality=7,
        macro_block_size=None,
        ffmpeg_params=["-crf", "23", "-preset", "fast"],
    )
    for frame in frames:
        writer.append_data(frame)
    writer.close()

    with open(tmp_path, "rb") as f:
        data = f.read()
    tmp_path.unlink(missing_ok=True)
    return data


def _generate_minimal_mp4(duration_seconds: int = 6) -> bytes:
    """Tiny placeholder MP4 when imageio/ffmpeg unavailable."""
    try:
        import imageio
        import numpy as np
        from PIL import Image
        import tempfile

        img = Image.new("RGB", (512, 512), (30, 30, 50))
        frame = np.array(img)
        fps = 8
        n = max(fps * duration_seconds, 16)
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
            path = tmp.name
        writer = imageio.get_writer(path, fps=fps, codec="libx264", macro_block_size=1)
        for _ in range(n):
            writer.append_data(frame)
        writer.close()
        with open(path, "rb") as f:
            data = f.read()
        os.unlink(path)
        return data
    except Exception:
        # Last resort: empty bytes — caller should log; prefer pip install imageio
        raise RuntimeError(
            "Video fallback requires: pip install imageio imageio-ffmpeg"
        )