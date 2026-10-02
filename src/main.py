"""Polite, cache-backed Books to Scrape assignment pipeline."""

from __future__ import annotations

import argparse
import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, ValidationError


ROOT = Path(__file__).resolve().parents[1]
BASE_URL = "https://books.toscrape.com/"
FIRST_PAGE = urljoin(BASE_URL, "catalogue/page-1.html")
USER_AGENT = "FlyRankInternship-A9/1.0 (educational scraper; https://github.com/ahmadfiazahmad/Polite_scraper)"
TIMEOUT_SECONDS = 10
MIN_DELAY_SECONDS = 0.5
MAX_CATALOGUE_PAGES = 3
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)


class BookRecord(BaseModel):
    """Normalized, validated record suitable for books.json."""

    model_config = ConfigDict(str_strip_whitespace=True)

    title: str = Field(min_length=1)
    product_url: HttpUrl
    price_text: str = Field(min_length=1)
    price_gbp: float = Field(ge=0)
    availability_text: str = Field(min_length=1)
    rating_text: str = Field(min_length=1)
    description: str | None
    source_page: HttpUrl
    fetched_at: datetime


class Fetcher:
    """GET helper with a file cache, honest counters, pacing, and one retry."""

    def __init__(self, cache_dir: Path, delay: float = MIN_DELAY_SECONDS) -> None:
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.delay = delay
        self.last_request_at: float | None = None
        self.pages_fetched = 0
        self.cache_hits = 0
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})

    def get(self, url: str, cache_name: str) -> tuple[str, bool]:
        cache_path = self.cache_dir / cache_name
        if cache_path.exists():
            self.cache_hits += 1
            logger.info("CACHE HIT %s (%d bytes)", cache_name, cache_path.stat().st_size)
            return cache_path.read_text(encoding="utf-8"), True

        for attempt in (1, 2):
            if self.last_request_at is not None:
                remaining = self.delay - (time.monotonic() - self.last_request_at)
                if remaining > 0:
                    time.sleep(remaining)
            self.last_request_at = time.monotonic()
            self.pages_fetched += 1
            try:
                response = self.session.get(url, timeout=TIMEOUT_SECONDS)
                logger.info("FETCH %s status=%d bytes=%d", url, response.status_code, len(response.content))
                response.raise_for_status()
                if response.status_code != 200:
                    raise requests.HTTPError(f"Expected HTTP 200, received {response.status_code}", response=response)
                # The sandbox serves UTF-8 HTML, while response headers can
                # otherwise make requests guess ISO-8859-1 for the pound sign.
                html = response.content.decode("utf-8")
                cache_path.write_text(html, encoding="utf-8")
                return html, False
            except requests.Timeout:
                if attempt == 2:
                    raise
                logger.warning("Timeout; retrying once: %s", url)
                time.sleep(1)
            except requests.HTTPError as exc:
                status = exc.response.status_code if exc.response is not None else None
                if status is None or status < 500 or attempt == 2:
                    raise
                logger.warning("HTTP %s; retrying once: %s", status, url)
                time.sleep(1)
        raise RuntimeError(f"Fetch exhausted retries for {url}")


def clean_price(price_text: str) -> float:
    match = re.search(r"\d+(?:\.\d{1,2})?", price_text.replace(",", ""))
    if not match:
        raise ValueError(f"Could not parse GBP price from {price_text!r}")
    return float(match.group())


def rating_from_product(article: Any) -> str:
    rating = article.select_one(".star-rating")
    if rating is None:
        raise ValueError("Missing product star rating")
    return next((name for name in rating.get("class", []) if name != "star-rating"), "")


def discover_catalogue(fetcher: Fetcher) -> tuple[list[tuple[str, str]], int]:
    """Return unique (book URL, source catalogue URL) pairs from first 3 pages."""
    page_url = FIRST_PAGE
    discovered: list[tuple[str, str]] = []
    seen: set[str] = set()
    catalogue_pages = 0
    for page_number in range(1, MAX_CATALOGUE_PAGES + 1):
        html, _ = fetcher.get(page_url, f"catalogue-page-{page_number}.html")
        soup = BeautifulSoup(html, "html.parser")
        catalogue_pages += 1
        for anchor in soup.select("ol.row article.product_pod h3 a[href]"):
            product_url = urljoin(page_url, anchor["href"])
            if product_url not in seen:
                seen.add(product_url)
                discovered.append((product_url, page_url))
        next_link = soup.select_one("li.next a[href]")
        if page_number < MAX_CATALOGUE_PAGES:
            if next_link is None:
                raise ValueError(f"Catalogue page {page_number} has no next-page link")
            page_url = urljoin(page_url, next_link["href"])
    return discovered, catalogue_pages


def parse_book(html: str, product_url: str, source_page: str) -> BookRecord:
    soup = BeautifulSoup(html, "html.parser")
    product = soup.select_one(".product_main")
    if product is None:
        raise ValueError("Missing product details area (.product_main)")
    title = product.select_one("h1")
    price = product.select_one(".price_color")
    availability = product.select_one(".availability")
    if title is None or price is None or availability is None:
        raise ValueError("Product page is missing title, price, or availability")
    description_node = soup.select_one("#product_description + p")
    description = description_node.get_text(" ", strip=True) if description_node else None
    return BookRecord(
        title=title.get_text(" ", strip=True),
        product_url=product_url,
        price_text=price.get_text(" ", strip=True),
        price_gbp=clean_price(price.get_text(" ", strip=True)),
        availability_text=availability.get_text(" ", strip=True),
        rating_text=rating_from_product(product),
        description=description,
        source_page=source_page,
        fetched_at=datetime.now(timezone.utc),
    )


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def run(include_broken_probe: bool = False) -> dict[str, Any]:
    started_at = datetime.now(timezone.utc)
    timer = time.monotonic()
    fetcher = Fetcher(ROOT / "cache")
    errors: list[dict[str, str]] = []
    records: dict[str, dict[str, Any]] = {}
    try:
        books, catalogue_pages = discover_catalogue(fetcher)
        logger.info("CHECKPOINT catalogue_pages=%d discovered=%d unique_urls=%d", catalogue_pages, len(books), len({u for u, _ in books}))
        if not books:
            raise RuntimeError("No book URLs discovered; check network and cached catalogue pages")
        print("CHECKPOINT catalogue_pages={} discovered={} unique_urls={}".format(catalogue_pages, len(books), len({u for u, _ in books})))
        first_record_printed = False
        for index, (url, source_page) in enumerate(books, start=1):
            try:
                html, _ = fetcher.get(url, f"book-{index:03d}.html")
                record = parse_book(html, url, source_page)
                records[str(record.product_url)] = record.model_dump(mode="json")
                if not first_record_printed:
                    print("CHECKPOINT sample_record=" + json.dumps(record.model_dump(mode="json"), ensure_ascii=False, indent=2))
                    first_record_printed = True
            except (requests.RequestException, ValueError, ValidationError) as exc:
                logger.error("Book failed url=%s error=%s", url, exc)
                errors.append({"url": url, "failure_type": type(exc).__name__, "details": str(exc)})
        if include_broken_probe:
            fake_url = urljoin(BASE_URL, "catalogue/assignment-deliberate-missing-page.html")
            try:
                fetcher.get(fake_url, "deliberate-missing-probe.html")
            except requests.RequestException as exc:
                errors.append({"url": fake_url, "failure_type": type(exc).__name__, "details": str(exc)})
                logger.info("Deliberate failure probe recorded: %s", fake_url)
    except Exception as exc:
        errors.append({"url": FIRST_PAGE, "failure_type": type(exc).__name__, "details": str(exc)})
        logger.exception("Catalogue run failed")

    elapsed = round(time.monotonic() - timer, 3)
    # Keep the deliberate failure demonstration separate from the clean sample
    # outputs produced by the normal run.
    output = ROOT / "output"
    if include_broken_probe:
        output = output / "failure-probe"
    write_json(output / "books.json", list(records.values()))
    write_json(output / "errors.json", errors)
    report = {
        "started_at": started_at.isoformat().replace("+00:00", "Z"),
        "duration_seconds": elapsed,
        "catalogue_pages_processed": locals().get("catalogue_pages", 0),
        "pages_fetched": fetcher.pages_fetched,
        "cache_hits": fetcher.cache_hits,
        "discovered_books": len(locals().get("books", [])),
        "unique_urls": len({u for u, _ in locals().get("books", [])}),
        "valid_records": len(records),
        "invalid_records": sum(e["failure_type"] == "ValidationError" for e in errors),
        "failed_pages": len(errors),
    }
    write_json(output / "run-report.json", report)
    print(f"CHECKPOINT detail_pages={len(locals().get('books', []))}")
    print(json.dumps(report, indent=2))
    return report


def check_robots() -> None:
    """Perform the one explicit Stage 0 robots.txt request and print its result."""
    try:
        response = requests.get(f"{BASE_URL}robots.txt", headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT_SECONDS)
        print(f"robots.txt HTTP {response.status_code}")
        print(response.text if response.status_code == 200 else "No readable robots policy returned.")
    except requests.RequestException as exc:
        print(f"robots.txt check failed: {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-robots", action="store_true", help="perform the Stage 0 robots.txt check")
    parser.add_argument("--broken-probe", action="store_true", help="include one deliberately broken URL to prove resilience")
    args = parser.parse_args()
    if args.check_robots:
        check_robots()
    else:
        run(include_broken_probe=args.broken_probe)
