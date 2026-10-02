# The polite scraper

This beginner-friendly Python project completes FlyRank Week 5 (A9). It collects book details from the first three catalogue pages of [Books to Scrape](https://books.toscrape.com/), validates them with Pydantic, and saves JSON files. The [ToScrape sandbox page](https://toscrape.com/) describes Books as a fictional bookstore intended for scraping practice.

## What it collects

Each valid record has the title, absolute product URL, original price text, numeric GBP price, availability, rating, description (null if absent), source catalogue page, and fetch timestamp. Pydantic checks the record before it is saved. Duplicate product URLs are kept once.

## Polite requests and cache

The scraper identifies itself with a User-Agent, uses a 10-second timeout, checks HTTP status, and waits at least 500 ms between real requests so it does not send requests too quickly. It retries a timeout or server error (5xx) once; 403 and 404 are not retried. Catalogue and book HTML are saved in `cache/`. Later runs use those files without requesting the same pages again or waiting for cache hits.

Stage 0 checked `https://books.toscrape.com/robots.txt` with Python `requests`; it returned HTTP 404 (no robots file found). A missing robots file is not permission by itself. This project only targets the public practice sandbox. I will not reuse this code on another site without checking its rules and terms first.

## Install

From this `scraper/` directory:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

## NORMAL RUN

Run:

```bash
python src/main.py
```

This writes the clean sample files:

- `output/books.json` — validated, unique book records.
- `output/errors.json` — errors from the normal run; should be an empty list on a clean run.
- `output/run-report.json` — counts, cache/network activity, and duration.

Expected checkpoint: `catalogue_pages=3`, `discovered=60`, `unique_urls=60`, `detail_pages=60`; the final report should show 60 valid records and 0 failed pages. Run the command again to confirm the same 60 records are read mostly from cache.

## FAILURE/RESILIENCE TEST

Run separately:

```bash
python src/main.py --broken-probe
```

This requests one deliberately missing page after processing the 60 books. It must finish, retain all valid books, and record the failed URL and reason. The test writes its files under `output/failure-probe/` so it does not replace the clean files under `output/`:

- `output/failure-probe/books.json`
- `output/failure-probe/errors.json`
- `output/failure-probe/run-report.json`

Expected failure-test report: 60 valid records and 1 failed page. The failure entry should be in `output/failure-probe/errors.json`.

## Repository URL TODO

The Week 5 GitHub repository URL is not present in this project, so no URL has been invented. Before publishing, replace `repository URL TODO` in `src/main.py`'s `USER_AGENT` with the real repository URL. The same real URL should be used as the User-Agent contact link.

## Ethics and limitation

Use an official API when one exists; never bypass logins, paywalls, or blocks; collect only what you need. The parser relies on the sandbox's current HTML selectors, which may need updating if the site changes.
