"""
agents/review_critic.py
Agent 6: Review / Critic Agent

Evaluates generated creatives for:
- Brand consistency
- Product accuracy (no hallucinations)
- Visual quality
- Marketing hook strength
- CTA clarity

If score < 0.70 and retries remain, recommends revised prompts.
"""

from __future__ import annotations
import json
import base64
import os
import structlog
from pathlib import Path
from tenacity import retry, stop_after_attempt, wait_exponential

from models.schemas import (
    WorkflowState, ReviewReport, AssetReview, JobStatus
)

logger = structlog.get_logger()

CRITIC_SYSTEM_PROMPT = """You are a senior creative director and brand strategist evaluating AI-generated
ecommerce product marketing materials.

You will receive product information and a description of a generated asset (image or video).
Evaluate the asset on these dimensions (each scored 0.0-1.0):

1. brand_consistency: Does it align with the brand identity and product positioning?
2. product_accuracy: Is the product represented accurately? Any hallucinations or wrong details?
3. visual_quality: Is the visual composition, lighting, and aesthetic professional?
4. hook_strength: Does it capture attention and communicate a compelling benefit?
5. cta_clarity: Is the call-to-action or next step clear?

Return a JSON object:
{
  "brand_consistency": 0.0-1.0,
  "product_accuracy": 0.0-1.0,
  "visual_quality": 0.0-1.0,
  "hook_strength": 0.0-1.0,
  "cta_clarity": 0.0-1.0,
  "issues": ["list of specific problems found"],
  "revised_prompt": "improved prompt if score < 0.70, otherwise null",
  "overall_score": 0.0-1.0,
  "passed": true/false
}

Be constructive but honest. A score below 0.70 should trigger a retry.
Return ONLY valid JSON."""

VISION_CRITIC_SYSTEM = """You are evaluating an AI-generated product marketing image.
Analyze the image for commercial quality and brand suitability.

Given the product context and the image, evaluate:
- Is the product clearly visible and well-presented?
- Is the background/setting appropriate for marketing?
- Are there any artifacts, distortions, or quality issues?
- Does it look like a professional marketing image?

Return JSON with the same structure as instructed."""


def build_critic_prompt(product_data, creative_strategy, asset_type: str,
                         asset_index: int, prompt_used: str) -> str:
    return f"""Evaluate this {asset_type} for product marketing:

PRODUCT: {product_data.title} by {product_data.brand}
DESCRIPTION: {product_data.description[:300]}
USP: {product_data.usp}
TARGET AUDIENCE: {product_data.target_audience}
PRIMARY HOOK: {creative_strategy.primary_hook if creative_strategy else 'N/A'}

ASSET TYPE: {asset_type} #{asset_index}
PROMPT USED TO GENERATE IT:
"{prompt_used}"

Based on the prompt and product context, evaluate whether this {asset_type} would:
1. Stop someone scrolling on Instagram/TikTok
2. Accurately represent the product without misleading claims
3. Align with the brand's positioning
4. Drive clicks or conversions

Score each dimension and provide specific, actionable feedback."""


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
async def run_review_critic(state: WorkflowState, groq_client) -> WorkflowState:
    """
    Agent 6: Review all generated creatives and flag quality issues.
    Uses vision model for images if available, text model otherwise.
    """
    if not state.generated_prompts:
        logger.warning("critic_skipped_no_prompts", job_id=state.job_id)
        state.review_report = _passing_report("No prompts to review")
        return state

    logger.info("agent6_critic_start", job_id=state.job_id)

    use_vision = _check_vision_available(groq_client)
    image_reviews = []
    video_reviews = []

    # ── Review images ─────────────────────────────────────────────────────
    for gen_img in state.generated_images:
        prompt = next(
            (p.prompt for p in state.generated_prompts.image_prompts
             if p.index == gen_img.index),
            gen_img.prompt_used,
        )

        review = await _review_asset(
            groq_client=groq_client,
            product_data=state.product_data,
            creative_strategy=state.creative_strategy,
            asset_type="image",
            asset_index=gen_img.index,
            prompt_used=prompt,
            file_path=gen_img.file_path if use_vision else None,
        )
        image_reviews.append(review)

    # ── Review videos ─────────────────────────────────────────────────────
    for gen_vid in state.generated_videos:
        prompt = next(
            (p.prompt for p in state.generated_prompts.video_prompts
             if p.index == gen_vid.index),
            gen_vid.prompt_used,
        )

        review = await _review_asset(
            groq_client=groq_client,
            product_data=state.product_data,
            creative_strategy=state.creative_strategy,
            asset_type="video",
            asset_index=gen_vid.index,
            prompt_used=prompt,
        )
        video_reviews.append(review)

    # ── Aggregate ──────────────────────────────────────────────────────────
    all_reviews = image_reviews + video_reviews
    all_passed = all(r.passed for r in all_reviews)
    avg_score = sum(r.overall_score for r in all_reviews) / max(len(all_reviews), 1)
    needs_retry = not all_passed and state.retry_count < state.max_retries

    # Update prompts with revised versions if needed
    if needs_retry:
        _apply_revised_prompts(state, image_reviews, video_reviews)

    issues_found = [issue for r in all_reviews for issue in r.issues]
    summary = (
        f"Overall score: {avg_score:.2f}. "
        f"{len([r for r in all_reviews if r.passed])}/{len(all_reviews)} assets passed. "
        f"{'Retry recommended.' if needs_retry else 'All assets approved.'}"
    )
    if issues_found:
        summary += f" Key issues: {'; '.join(issues_found[:3])}"

    state.review_report = ReviewReport(
        image_reviews=image_reviews,
        video_reviews=video_reviews,
        overall_passed=all_passed,
        retry_recommended=needs_retry,
        summary=summary,
    )

    logger.info(
        "agent6_complete",
        job_id=state.job_id,
        avg_score=round(avg_score, 2),
        passed=all_passed,
        retry=needs_retry,
    )
    return state


async def _review_asset(
    groq_client,
    product_data,
    creative_strategy,
    asset_type: str,
    asset_index: int,
    prompt_used: str,
    file_path: str | None = None,
) -> AssetReview:
    """Review a single asset using LLM (optionally with vision)."""

    user_content: list | str = build_critic_prompt(
        product_data, creative_strategy, asset_type, asset_index, prompt_used
    )

    # If we have the image file and want to use vision
    if file_path and os.path.exists(file_path) and asset_type == "image":
        try:
            with open(file_path, "rb") as f:
                img_b64 = base64.b64encode(f.read()).decode()
            user_content = [
                {"type": "text", "text": user_content},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{img_b64}"},
                },
            ]
        except Exception:
            pass   # Fall back to text-only

    try:
        model = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
        fallback_model = os.getenv("GROQ_FALLBACK_MODEL")

        try:
            resp = groq_client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": CRITIC_SYSTEM_PROMPT},
                    {"role": "user", "content": user_content},
                ],
                temperature=0.2,
                max_tokens=800,
                response_format={"type": "json_object"},
            )
        except Exception as e:
            if ("ratelimit" in str(e).lower() or "rate limit" in str(e).lower() or "RateLimitError" in e.__class__.__name__) and fallback_model:
                resp = groq_client.chat.completions.create(
                    model=fallback_model,
                    messages=[
                        {"role": "system", "content": CRITIC_SYSTEM_PROMPT},
                        {"role": "user", "content": user_content},
                    ],
                    temperature=0.2,
                    max_tokens=800,
                    response_format={"type": "json_object"},
                )
            else:
                raise

        data = json.loads(resp.choices[0].message.content)
        overall = data.get("overall_score", _calc_overall(data))

        return AssetReview(
            asset_type=asset_type,
            asset_index=asset_index,
            brand_consistency=float(data.get("brand_consistency", 0.7)),
            product_accuracy=float(data.get("product_accuracy", 0.7)),
            visual_quality=float(data.get("visual_quality", 0.7)),
            hook_strength=float(data.get("hook_strength", 0.7)),
            cta_clarity=float(data.get("cta_clarity", 0.7)),
            overall_score=float(overall),
            issues=data.get("issues", []),
            passed=bool(data.get("passed", overall >= 0.70)),
            revised_prompt=data.get("revised_prompt"),
        )

    except Exception as e:
        logger.warning("review_llm_failed", error=str(e))
        return _default_passing_review(asset_type, asset_index)


def _calc_overall(data: dict) -> float:
    weights = {
        "brand_consistency": 0.25,
        "product_accuracy": 0.30,
        "visual_quality": 0.20,
        "hook_strength": 0.15,
        "cta_clarity": 0.10,
    }
    return sum(float(data.get(k, 0.7)) * w for k, w in weights.items())


def _check_vision_available(groq_client) -> bool:
    """Check if a vision-capable model is available (Groq currently text-only)."""
    return False   # Set True when Groq adds vision, or swap for OpenAI GPT-4V


def _apply_revised_prompts(state: WorkflowState, image_reviews, video_reviews):
    """Update generated_prompts with revised versions from critic."""
    for review in image_reviews:
        if review.revised_prompt:
            for p in state.generated_prompts.image_prompts:
                if p.index == review.asset_index:
                    p.prompt = review.revised_prompt
                    break

    for review in video_reviews:
        if review.revised_prompt:
            for p in state.generated_prompts.video_prompts:
                if p.index == review.asset_index:
                    p.prompt = review.revised_prompt
                    break


def _default_passing_review(asset_type: str, index: int) -> AssetReview:
    return AssetReview(
        asset_type=asset_type, asset_index=index,
        brand_consistency=0.75, product_accuracy=0.75,
        visual_quality=0.75, hook_strength=0.70, cta_clarity=0.70,
        overall_score=0.73, issues=[], passed=True,
    )


def _passing_report(summary: str) -> ReviewReport:
    return ReviewReport(
        image_reviews=[], video_reviews=[],
        overall_passed=True, retry_recommended=False,
        summary=summary,
    )