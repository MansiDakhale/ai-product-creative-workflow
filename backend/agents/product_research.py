"""
agents/product_research.py
Agent 1: Product Research Agent

Scrapes the product URL and uses an LLM to extract structured product data
including title, features, specifications, pricing, reviews, and brand positioning.
"""

from __future__ import annotations
import json
import structlog
from tenacity import retry, stop_after_attempt, wait_exponential

from pydantic import ValidationError

from models.schemas import WorkflowState, ProductData, ProductReview, JobStatus
from utils.scraper import scrape_product_page

logger = structlog.get_logger()

PRODUCT_RESEARCH_SYSTEM_PROMPT = """You are an expert ecommerce product analyst.
Your job is to extract and understand product information from scraped webpage content.

Given raw product page data, extract a structured JSON object with these fields:
- title: Product name (string)
- brand: Brand name (string)
- description: Detailed product description (string, 2-4 sentences)
- features: Array of key product features/benefits (list of strings, max 8)
- specifications: Dict of technical specs (key-value pairs)
- price: Price with currency symbol (string)
- currency: ISO currency code (string)
- category: Product category (string)
- reviews: Object with {rating: float, summary: string, count: int} or null
- target_audience: Who this product is for (string)
- usp: Unique Selling Proposition — the single most compelling reason to buy (string)

Return ONLY valid JSON. No markdown, no explanation.
"""


def _coerce_product_dict(data: dict) -> dict:
    """Normalize LLM output: nulls → defaults, specs values → strings."""
    cleaned = {k: v for k, v in data.items() if k in ProductData.model_fields}
    for key, default in (
        ("brand", ""),
        ("description", ""),
        ("price", ""),
        ("currency", "USD"),
        ("category", ""),
        ("target_audience", ""),
        ("usp", ""),
        ("raw_html_snippet", ""),
        ("source_url", ""),
        ("title", "Unknown Product"),
    ):
        if cleaned.get(key) is None:
            cleaned[key] = default
    if cleaned.get("features") is None:
        cleaned["features"] = []
    if cleaned.get("specifications") is None:
        cleaned["specifications"] = {}
    elif isinstance(cleaned["specifications"], dict):
        cleaned["specifications"] = {
            str(k): str(v) if v is not None else ""
            for k, v in cleaned["specifications"].items()
        }
    return cleaned


def _product_from_scraped(scraped: dict, url: str) -> ProductData:
    return ProductData(
        title=scraped.get("title") or "Unknown Product",
        brand=scraped.get("brand") or "",
        description=scraped.get("description") or "",
        features=scraped.get("features") or [],
        specifications=scraped.get("specifications") or {},
        price=scraped.get("price") or "",
        source_url=url,
    )


def build_research_prompt(scraped_data: dict) -> str:
    return f"""Analyze this product page data and extract structured information:

URL: {scraped_data.get('source_url', '')}
Title found: {scraped_data.get('title', 'Not found')}
Price found: {scraped_data.get('price', 'Not found')}
Brand found: {scraped_data.get('brand', 'Not found')}

Raw features detected:
{json.dumps(scraped_data.get('features', []), indent=2)}

Specifications detected:
{json.dumps(scraped_data.get('specifications', {}), indent=2)}

Description excerpt:
{scraped_data.get('description', '')[:800]}

Full page text excerpt:
{scraped_data.get('raw_text', '')[:2000]}

Reviews data: {json.dumps(scraped_data.get('reviews', {}), indent=2)}

Extract comprehensive product information and return as JSON."""


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
async def run_product_research(state: WorkflowState, groq_client) -> WorkflowState:
    """
    Agent 1: Scrape the URL and extract structured product data using LLM.
    
    Args:
        state: Current workflow state
        groq_client: Initialized Groq client
    
    Returns:
        Updated workflow state with product_data populated
    """
    logger.info("agent1_product_research_start", job_id=state.job_id, url=state.url)
    state.status = JobStatus.RUNNING

    # Step 1: Scrape the product page
    try:
        scraped = await scrape_product_page(state.url)
        logger.info("scrape_complete", job_id=state.job_id, title=scraped.get("title"))
    except Exception as e:
        logger.error("scrape_failed", job_id=state.job_id, error=str(e))
        state.error = f"Scraping failed: {str(e)}"
        state.status = JobStatus.FAILED
        return state

    # Step 2: LLM extraction for structured product understanding
    try:
        user_prompt = build_research_prompt(scraped)
        response = groq_client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[
                {"role": "system", "content": PRODUCT_RESEARCH_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.1,
            max_tokens=1500,
            response_format={"type": "json_object"},
        )

        raw_json = response.choices[0].message.content
        product_dict = json.loads(raw_json)

        # Merge LLM output with scraped data (LLM fills gaps)
        product_dict.setdefault("source_url", state.url)
        product_dict.setdefault("raw_html_snippet", scraped.get("raw_text", "")[:500])

        # Build reviews sub-object if present
        reviews_raw = product_dict.pop("reviews", None)
        reviews = None
        if reviews_raw and isinstance(reviews_raw, dict):
            reviews = ProductReview(
                rating=float(reviews_raw.get("rating", 0)),
                summary=reviews_raw.get("summary", ""),
                count=int(reviews_raw.get("count", 0)),
            )

        state.product_data = ProductData(
            **_coerce_product_dict(product_dict),
            reviews=reviews,
        )

        logger.info(
            "agent1_complete",
            job_id=state.job_id,
            title=state.product_data.title,
            brand=state.product_data.brand,
            usp=state.product_data.usp[:80] if state.product_data.usp else "",
        )

    except (json.JSONDecodeError, ValidationError) as e:
        logger.warning("product_data_fallback", error=str(e), job_id=state.job_id)
        state.product_data = _product_from_scraped(scraped, state.url)
    except Exception as e:
        # If Groq rate limiting or other API errors occur, stop retrying and
        # mark the workflow as failed with a clear error message. Tenacity
        # retries are useful for transient failures, but rate limits should
        # surface to the user rather than looping.
        err_name = e.__class__.__name__
        if "RateLimitError" in err_name or "rate limit" in str(e).lower():
            logger.error("agent1_rate_limited", job_id=state.job_id, error=str(e))
            state.error = f"Product research failed: rate limited by LLM provider: {str(e)}"
            state.status = JobStatus.FAILED
            return state
        # Re-raise other exceptions to allow tenacity to retry per decorator
        raise

    return state