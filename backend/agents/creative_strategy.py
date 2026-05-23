"""
agents/creative_strategy.py
Agent 2: Creative Strategy Agent

Generates comprehensive creative directions for ads including:
hooks, audience targeting angles, visual themes, captions, and marketing messaging.
"""

from __future__ import annotations
import json
import os
import structlog
from tenacity import retry, stop_after_attempt, wait_exponential

from models.schemas import (
    WorkflowState, CreativeStrategy, AudienceAngle,
    VisualTheme, JobStatus
)

logger = structlog.get_logger()

CREATIVE_STRATEGY_SYSTEM_PROMPT = """You are a world-class creative director and performance marketing strategist
specializing in ecommerce brands. You combine the creative instincts of a top ad agency with
the data-driven mindset of a growth marketer.

Given product information, generate a comprehensive creative strategy for social media ads.
Your output should be a JSON object with these fields:

{
  "primary_hook": "The single strongest opening line / hook for this product",
  "secondary_hooks": ["hook2", "hook3", "hook4"],
  "audience_angles": [
    {
      "segment": "Describe the target segment",
      "pain_point": "Their specific pain this product solves",
      "hook": "Hook tailored to this audience",
      "cta": "Call to action for this segment"
    }
  ],
  "visual_themes": [
    {
      "name": "Theme name",
      "color_palette": ["#hex1", "#hex2", "#hex3"],
      "mood": "Description of emotional mood",
      "composition_notes": "What to show, how to compose the image/video"
    }
  ],
  "caption_ideas": ["caption1", "caption2", "caption3"],
  "hashtag_suggestions": ["#tag1", "#tag2"],
  "platform_notes": {
    "instagram": "What works on Instagram for this product",
    "tiktok": "What works on TikTok for this product",
    "facebook": "What works on Facebook Ads"
  }
}

Generate 3 audience_angles and 3 visual_themes.
Be specific, creative, and emotionally resonant. Avoid generic marketing clichés.
Return ONLY valid JSON. No markdown, no explanation.
"""


def build_strategy_prompt(product_data, extra_instructions: str | None = None) -> str:
    features_text = "\n".join(f"• {f}" for f in product_data.features) if product_data.features else "Not specified"
    specs_text = "\n".join(f"  {k}: {v}" for k, v in product_data.specifications.items()) if product_data.specifications else "Not specified"
    reviews_text = ""
    if product_data.reviews:
        reviews_text = f"Rating: {product_data.reviews.rating}/5 ({product_data.reviews.count} reviews)\n{product_data.reviews.summary}"

    extra = f"\nEXTRA INSTRUCTIONS: {extra_instructions}" if extra_instructions else ""

    return f"""Create a complete creative strategy for this product:

PRODUCT: {product_data.title}
BRAND: {product_data.brand}
CATEGORY: {product_data.category}
PRICE: {product_data.price}

DESCRIPTION:
{product_data.description}

KEY FEATURES:
{features_text}

SPECIFICATIONS:
{specs_text}

TARGET AUDIENCE: {product_data.target_audience}
USP: {product_data.usp}

REVIEWS: {reviews_text if reviews_text else 'No review data available'}{extra}

Generate creative strategy that makes people stop scrolling and want to buy immediately.
Think about what emotions this product triggers, what transformation it enables,
and what aspirational identity it represents."""


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
async def run_creative_strategy(state: WorkflowState, groq_client) -> WorkflowState:
    """
    Agent 2: Generate creative directions from product data.
    
    Args:
        state: Workflow state (must have product_data populated)
        groq_client: Initialized Groq client
    
    Returns:
        Updated workflow state with creative_strategy populated
    """
    if not state.product_data:
        state.error = "Cannot run creative strategy: product_data is missing"
        state.status = JobStatus.FAILED
        return state

    logger.info("agent2_creative_strategy_start", job_id=state.job_id)

    try:
        user_prompt = build_strategy_prompt(state.product_data, state.extra_instructions)

        model = os.getenv("GROQ_MODEL", "llama3-8b-8192")
        fallback_model = os.getenv("GROQ_FALLBACK_MODEL")

        try:
            response = groq_client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": CREATIVE_STRATEGY_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.75,   # Higher creativity for strategy
                max_tokens=2500,
                response_format={"type": "json_object"},
            )
        except Exception as e:
            # If rate limited, try fallback model once; otherwise raise
            if ("ratelimit" in str(e).lower() or "rate limit" in str(e).lower() or "RateLimitError" in e.__class__.__name__) and fallback_model:
                response = groq_client.chat.completions.create(
                    model=fallback_model,
                    messages=[
                        {"role": "system", "content": CREATIVE_STRATEGY_SYSTEM_PROMPT},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=0.75,
                    max_tokens=2500,
                    response_format={"type": "json_object"},
                )
            else:
                raise

        raw_json = response.choices[0].message.content
        strategy_dict = json.loads(raw_json)

        # Parse nested objects
        audience_angles = [
            AudienceAngle(**a) for a in strategy_dict.get("audience_angles", [])
        ]
        visual_themes = [
            VisualTheme(**v) for v in strategy_dict.get("visual_themes", [])
        ]

        state.creative_strategy = CreativeStrategy(
            primary_hook=strategy_dict.get("primary_hook", ""),
            secondary_hooks=strategy_dict.get("secondary_hooks", []),
            audience_angles=audience_angles,
            visual_themes=visual_themes,
            caption_ideas=strategy_dict.get("caption_ideas", []),
            hashtag_suggestions=strategy_dict.get("hashtag_suggestions", []),
            platform_notes=strategy_dict.get("platform_notes", {}),
        )

        logger.info(
            "agent2_complete",
            job_id=state.job_id,
            primary_hook=state.creative_strategy.primary_hook[:80],
            themes=[t.name for t in state.creative_strategy.visual_themes],
        )

    except Exception as e:
        # Treat rate-limit errors as terminal for this job (don't keep retrying)
        if "ratelimit" in str(e).lower() or "rate limit" in str(e).lower() or "RateLimitError" in e.__class__.__name__:
            logger.error("agent2_rate_limited", job_id=state.job_id, error=str(e))
            state.error = f"Creative strategy failed: rate limited by LLM provider: {str(e)}"
            state.status = JobStatus.FAILED
            return state

        logger.error("agent2_failed", job_id=state.job_id, error=str(e))
        state.error = f"Creative strategy failed: {str(e)}"
        state.status = JobStatus.FAILED

    return state