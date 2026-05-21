"""
utils/scraper.py
Robust product page scraper using crawl4ai + BeautifulSoup fallback.
"""

from __future__ import annotations
import re
import httpx
import asyncio
from bs4 import BeautifulSoup
import structlog

logger = structlog.get_logger()

# Common product-page selectors (works across Shopify, WooCommerce, Amazon, etc.)
TITLE_SELECTORS = [
    "h1.product-title", "h1.product_title", "h1#productTitle",
    "h1[itemprop='name']", ".pdp-title h1", "h1.title", "h1",
]
PRICE_SELECTORS = [
    "[itemprop='price']", ".price", "#priceblock_ourprice",
    ".product-price", ".woocommerce-Price-amount", "span.price",
]
DESCRIPTION_SELECTORS = [
    "[itemprop='description']", "#productDescription", ".product-description",
    ".woocommerce-product-details__short-description", ".pdp-description",
    "#feature-bullets", ".product-details",
]
REVIEW_SELECTORS = [
    "[itemprop='aggregateRating']", "#averageCustomerReviews",
    ".product-review-avg", ".reviews-summary",
]


async def scrape_product_page(url: str) -> dict:
    """
    Primary scraper. Tries crawl4ai first (JS-rendered pages),
    falls back to httpx + BeautifulSoup for static pages.
    """
    logger.info("scraping_url", url=url)

    # Try crawl4ai first (handles JS-heavy pages like Amazon)
    try:
        result = await _scrape_with_crawl4ai(url)
        if result and result.get("title"):
            logger.info("scraped_with_crawl4ai", title=result["title"])
            return result
    except Exception as e:
        logger.warning("crawl4ai_failed", error=str(e))

    # Fallback: plain httpx + BeautifulSoup
    try:
        result = await _scrape_with_httpx(url)
        if result:
            logger.info("scraped_with_httpx", title=result.get("title", ""))
            return result
    except Exception as e:
        logger.error("httpx_scrape_failed", error=str(e))

    return {"title": "", "description": "", "raw_text": "", "source_url": url}


async def _scrape_with_crawl4ai(url: str) -> dict:
    """Use crawl4ai for JS-rendered pages."""
    try:
        from crawl4ai import AsyncWebCrawler
        from crawl4ai.extraction_strategy import LLMExtractionStrategy
    except ImportError:
        raise RuntimeError("crawl4ai not installed")

    async with AsyncWebCrawler(verbose=False) as crawler:
        result = await crawler.arun(
            url=url,
            word_count_threshold=50,
            bypass_cache=True,
            timeout=30,
        )

    if not result.success:
        raise RuntimeError(f"crawl4ai failed: {result.error_message}")

    soup = BeautifulSoup(result.html, "html.parser")
    return _parse_soup(soup, url, result.markdown)


async def _scrape_with_httpx(url: str) -> dict:
    """Fallback: plain HTTP + BeautifulSoup."""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
    }
    from utils.http_client import make_async_client
    async with make_async_client(follow_redirects=True, timeout=30) as client:
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    # Remove script/style noise
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()

    return _parse_soup(soup, url, soup.get_text(separator="\n", strip=True))


def _parse_soup(soup: BeautifulSoup, url: str, raw_text: str) -> dict:
    """Extract structured product fields from parsed HTML."""

    def first_text(selectors: list[str]) -> str:
        for sel in selectors:
            el = soup.select_one(sel)
            if el:
                return el.get_text(strip=True)
        return ""

    title = first_text(TITLE_SELECTORS)
    price = first_text(PRICE_SELECTORS)
    description = first_text(DESCRIPTION_SELECTORS)

    # Features / bullet points
    features: list[str] = []
    for ul in soup.select("ul.product-features, #feature-bullets ul, .woocommerce-product-details__short-description ul"):
        for li in ul.find_all("li"):
            text = li.get_text(strip=True)
            if text and len(text) > 5:
                features.append(text)

    # Fallback feature extraction from raw text
    if not features and raw_text:
        lines = [l.strip() for l in raw_text.split("\n") if len(l.strip()) > 10]
        # Look for short, punchy lines that sound like features
        features = [l for l in lines if len(l) < 120 and not l.startswith("http")][:8]

    # Specifications (key:value tables)
    specs: dict[str, str] = {}
    for table in soup.select("table.product-specs, .specifications table, #productDetails table"):
        for row in table.find_all("tr"):
            cells = row.find_all(["td", "th"])
            if len(cells) >= 2:
                key = cells[0].get_text(strip=True)
                val = cells[1].get_text(strip=True)
                if key and val:
                    specs[key] = val

    # Reviews
    reviews: dict = {}
    rating_el = soup.select_one("[itemprop='ratingValue'], .rating-value, #acrPopover")
    if rating_el:
        try:
            reviews["rating"] = float(re.search(r"[\d.]+", rating_el.get_text())[0])
        except Exception:
            pass

    count_el = soup.select_one("[itemprop='reviewCount'], .review-count, #acrCustomerReviewText")
    if count_el:
        try:
            reviews["count"] = int(re.search(r"\d+", count_el.get_text())[0])
        except Exception:
            pass

    # Brand / meta
    brand = ""
    brand_el = soup.select_one("[itemprop='brand']") or soup.select_one(".brand")
    if brand_el:
        brand = brand_el.get_text(strip=True)

    # Trim raw text to avoid token explosion downstream
    trimmed_raw = raw_text[:3000] if raw_text else ""

    return {
        "title": title or _infer_title_from_url(url),
        "brand": brand,
        "description": description or trimmed_raw[:500],
        "features": features[:10],
        "specifications": specs,
        "price": price,
        "reviews": reviews if reviews else None,
        "raw_text": trimmed_raw,
        "source_url": url,
    }


def _infer_title_from_url(url: str) -> str:
    """Last resort: derive a readable title from the URL path."""
    from urllib.parse import urlparse
    path = urlparse(url).path
    slug = path.strip("/").split("/")[-1]
    return slug.replace("-", " ").replace("_", " ").title()