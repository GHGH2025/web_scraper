"""Login to Florida Off Market and list search cards (Playwright probe).

Usage:
  copy .env.example to .env and set FOM_EMAIL / FOM_PASSWORD
  python scrape_fom.py --headed          # first run / captcha
  python scrape_fom.py                   # headless, reuses saved session
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv
from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright

BASE_URL = "https://floridaoffmarket.mysharetribe.com"
SEARCH_PATH = "/s"
LOGIN_PATH = "/login"
LISTING_HREF_RE = re.compile(r"/l/([^/]+)/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", re.I)
PRICE_RE = re.compile(r"\$[\d,]+(?:\.\d{2})?")
PAGE_HREF_RE = re.compile(r"[?&]page=(\d+)", re.I)
GO_TO_PAGE_RE = re.compile(r"Go to page\s+(\d+)", re.I)
RESULTS_RE = re.compile(r"(\d+)\s+results?", re.I)

ROOT = Path(__file__).resolve().parent
SESSION_DIR = ROOT / ".session"
STATE_PATH = SESSION_DIR / "storage_state.json"
DATA_DIR = ROOT / "data"
LOG_DIR = ROOT / "logs"
DEFAULT_LOG_PATH = LOG_DIR / "scrape_fom.log"

log = logging.getLogger("fom")


def setup_logging(log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log.setLevel(logging.DEBUG)
    log.handlers.clear()
    fmt = logging.Formatter("%(asctime)s %(levelname)-5s %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(fmt)
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console.setFormatter(fmt)
    log.addHandler(file_handler)
    log.addHandler(console)


def _search_url(county: str | None = None, page: int = 1) -> str:
    url = f"{BASE_URL}{SEARCH_PATH}"
    params: list[str] = []
    if county:
        params.append(f"pub_listingcounty={county.strip().lower()}")
    if page > 1:
        params.append(f"page={page}")
    if params:
        url += "?" + "&".join(params)
    return url


def _load_creds() -> tuple[str, str]:
    env_path = ROOT / ".env"
    log.info("Loading credentials from %s", env_path)
    load_dotenv(env_path)
    email = (os.getenv("FOM_EMAIL") or "").strip()
    password = (os.getenv("FOM_PASSWORD") or "").strip()
    if not email or not password:
        log.error("FOM_EMAIL and FOM_PASSWORD must be set in scraper/.env (see .env.example)")
        sys.exit(1)
    log.info("Using account %s", email)
    return email, password


def _dismiss_banners(page) -> None:
    for name in ("Accept", "Accept all", "I agree", "Got it", "Close"):
        btn = page.get_by_role("button", name=name)
        try:
            if btn.count() and btn.first.is_visible():
                log.info("Dismissing banner button %r", name)
                btn.first.click(timeout=1500)
        except Exception as exc:
            log.debug("Banner %r not clicked: %s", name, exc)


def _login_form_visible(page) -> bool:
    email = page.get_by_label("Email", exact=True)
    try:
        return email.count() > 0 and email.first.is_visible()
    except Exception:
        return False


def _has_login_link(page) -> bool:
    link = page.get_by_role("link", name=re.compile(r"^Log in$", re.I))
    try:
        return link.count() > 0 and link.first.is_visible()
    except Exception:
        return False


def _submit_login(page, email: str, password: str, timeout_ms: int) -> None:
    log.info("Filling login form for %s", email)
    page.get_by_label("Email", exact=True).fill(email)
    page.get_by_label("Password", exact=True).fill(password)
    form = page.locator("form")
    submit = form.get_by_role("button", name="Log in")
    if submit.count():
        log.info("Clicking form Log in button")
        submit.first.click()
    else:
        log.info("Clicking page Log in button")
        page.get_by_role("button", name="Log in").first.click()
    log.info("Waiting to leave %s (timeout %sms)", LOGIN_PATH, timeout_ms)
    try:
        page.wait_for_url(lambda url: LOGIN_PATH not in url, timeout=timeout_ms)
    except PlaywrightTimeout:
        err = ""
        try:
            err = page.inner_text("body", timeout=2000)[:400]
        except Exception:
            pass
        log.error("Login did not leave %s. Current URL: %s", LOGIN_PATH, page.url)
        if err:
            log.error("Page text: %s", err)
        raise RuntimeError(
            "Still on the login page after submit. Check credentials or run with --headed.\n"
            f"{err}"
        )
    log.info("Login succeeded. Now at %s", page.url)


def _ensure_logged_in(page, email: str, password: str, timeout_ms: int) -> None:
    log.info("Checking login state. URL=%s", page.url)
    if _login_form_visible(page) or LOGIN_PATH in (page.url or ""):
        log.info("Login form is visible — signing in")
        _submit_login(page, email, password, timeout_ms)
        return
    if _has_login_link(page):
        login_url = f"{BASE_URL}{LOGIN_PATH}"
        log.info("Header still shows Log in — opening %s", login_url)
        page.goto(login_url, wait_until="domcontentloaded")
        log.info("Opened login page. URL=%s", page.url)
        _dismiss_banners(page)
        _submit_login(page, email, password, timeout_ms)
        return
    log.info("Already logged in (no login form or Log in link)")


def _wait_for_cards(page, timeout_ms: int) -> None:
    log.info("Waiting for listing cards on %s (timeout %sms)", page.url, timeout_ms)
    page.locator('a[href*="/l/"]').first.wait_for(state="visible", timeout=timeout_ms)
    log.info("Listing cards are visible")


def _wait_for_pagination(page) -> None:
    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    try:
        page.locator('a[href*="page="]').first.wait_for(state="visible", timeout=8000)
        log.info("Pagination links are visible")
    except PlaywrightTimeout:
        log.info("No pagination links visible")


def _page_numbers_from_links(page) -> int:
    pages = {1}
    for loc in page.locator('a[href*="page="]').all():
        href = loc.get_attribute("href") or ""
        href_match = PAGE_HREF_RE.search(href)
        if href_match:
            pages.add(int(href_match.group(1)))
        aria_match = GO_TO_PAGE_RE.search(loc.get_attribute("aria-label") or "")
        if aria_match:
            pages.add(int(aria_match.group(1)))
    return max(pages)


def _detect_total_pages(page, page_size: int) -> int:
    total = _page_numbers_from_links(page)
    try:
        body = page.inner_text("body", timeout=2000)
    except Exception:
        body = ""
    results_match = RESULTS_RE.search(body or "")
    result_count = int(results_match.group(1)) if results_match else None
    if result_count and page_size > 0:
        inferred = max(1, (result_count + page_size - 1) // page_size)
        log.info("Search reports %s results, page size %s → %s page(s)", result_count, page_size, inferred)
        total = max(total, inferred)
    log.info("Detected %s search page(s)", total)
    return total


def _absolute_listing_url(href: str) -> str:
    if href.startswith("http"):
        return href.split("?")[0]
    return f"{BASE_URL}{href.split('?')[0]}"


def _extract_cards(page) -> list[dict]:
    anchors = page.locator('a[href*="/l/"]')
    raw_count = anchors.count()
    log.info("Found %s /l/ links on the page", raw_count)
    seen: dict[str, dict] = {}
    for loc in anchors.all():
        href = loc.get_attribute("href") or ""
        match = LISTING_HREF_RE.search(href)
        if not match:
            log.debug("Skipping non-listing href %s", href)
            continue
        slug, listing_id = match.group(1), match.group(2)
        if listing_id in seen:
            continue
        url = _absolute_listing_url(href)
        try:
            text = " ".join((loc.inner_text(timeout=2000) or "").split())
        except Exception as exc:
            log.warning("Could not read card text for %s: %s", listing_id, exc)
            text = ""
        price_match = PRICE_RE.search(text)
        seen[listing_id] = {
            "listing_id": listing_id,
            "slug": slug,
            "url": url,
            "price": price_match.group(0) if price_match else None,
            "card_text": text,
        }
        log.info("Deal link %s  %s  %s", url, seen[listing_id]["price"] or "no-price", text[:80])
    log.info("Deduped to %s unique listings on this page", len(seen))
    return list(seen.values())


def _collect_all_listings(page, county: str | None, timeout_ms: int) -> list[dict]:
    by_id: dict[str, dict] = {}
    total_pages = None
    page_num = 1
    while True:
        url = _search_url(county, page_num)
        log.info("Opening search page %s/%s: %s", page_num, total_pages or "?", url)
        page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        log.info("Search page %s loaded. URL=%s", page_num, page.url)
        _dismiss_banners(page)
        try:
            _wait_for_cards(page, timeout_ms)
        except PlaywrightTimeout:
            if page_num == 1:
                raise
            log.info("No cards on page %s — stopping pagination", page_num)
            break
        _wait_for_pagination(page)
        cards = _extract_cards(page)
        if total_pages is None:
            total_pages = _detect_total_pages(page, len(cards))
            log.info("Will scrape all %s page(s)", total_pages)
        else:
            found = _page_numbers_from_links(page)
            if found > total_pages:
                log.info("Pagination now shows %s pages (was %s)", found, total_pages)
                total_pages = found
        new_count = 0
        for card in cards:
            if card["listing_id"] not in by_id:
                by_id[card["listing_id"]] = card
                new_count += 1
        log.info("Page %s added %s new deals (%s total)", page_num, new_count, len(by_id))
        if not cards or page_num >= total_pages:
            break
        page_num += 1
    links = [item["url"] for item in by_id.values()]
    pages_scraped = total_pages or page_num
    log.info("Collected %s deal links from %s page(s)", len(links), pages_scraped)
    for i, link in enumerate(links, start=1):
        log.info("  [%s] %s", i, link)
    return list(by_id.values()), pages_scraped


def run(headed: bool, county: str | None, timeout_ms: int, out_path: Path | None = None) -> dict:
    log.info("=== scrape start county=%s headed=%s timeout_ms=%s ===", county or "all", headed, timeout_ms)
    email, password = _load_creds()
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    search_url = _search_url(county)
    log.info("Search URL: %s", search_url)

    with sync_playwright() as p:
        log.info("Launching Chromium headless=%s", not headed)
        browser = p.chromium.launch(headless=not headed)
        context_kwargs = {
            "viewport": {"width": 1400, "height": 900},
        }
        if STATE_PATH.exists():
            log.info("Reusing saved session %s", STATE_PATH)
            context_kwargs["storage_state"] = str(STATE_PATH)
        else:
            log.info("No saved session — will log in fresh")
        context = browser.new_context(**context_kwargs)
        page = context.new_page()
        page.set_default_timeout(timeout_ms)

        log.info("Opening search page")
        page.goto(search_url, wait_until="domcontentloaded")
        log.info("Search page loaded. URL=%s", page.url)
        _dismiss_banners(page)
        _ensure_logged_in(page, email, password, timeout_ms)

        if LISTING_HREF_RE.search(page.url or "") is None and "/s" not in (page.url or ""):
            log.info("Not on search results after login. Returning to %s", search_url)
            page.goto(search_url, wait_until="domcontentloaded")
            log.info("Search page loaded. URL=%s", page.url)
            _dismiss_banners(page)

        logged_in = not _has_login_link(page) and not _login_form_visible(page)
        log.info("Session looks logged_in=%s", logged_in)
        cards, total_pages = _collect_all_listings(page, county, timeout_ms)
        if not cards:
            log.warning("No listing cards extracted")

        log.info("Saving browser session to %s", STATE_PATH)
        context.storage_state(path=str(STATE_PATH))
        context.close()
        browser.close()
        log.info("Browser closed")

    result = {
        "county": county,
        "search_url": search_url,
        "logged_in": logged_in,
        "total_pages": total_pages,
        "card_count": len(cards),
        "links": [item["url"] for item in cards],
        "listings": cards,
        "session_saved": str(STATE_PATH),
    }
    if out_path is not None:
        dest = Path(out_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(result, indent=2), encoding="utf-8")
        result["wrote"] = str(dest.resolve())
        log.info("Wrote listings JSON (%s deals) to %s", len(cards), result["wrote"])
    log.info("Scraped %s deals (Mongo is the store; JSON only if --out)", len(cards))
    log.info("=== scrape done ===")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Florida Off Market login + listing probe")
    parser.add_argument("--headed", action="store_true", help="Show the browser (use for first login)")
    parser.add_argument(
        "--county",
        default=None,
        help="Optional pub_listingcounty filter. Default: all listings at /s",
    )
    parser.add_argument("--timeout", type=int, default=45000, help="Timeout in ms")
    parser.add_argument(
        "--out",
        default=None,
        help="Optional JSON output path (daily job writes Mongo only)",
    )
    parser.add_argument(
        "--log-file",
        default=str(DEFAULT_LOG_PATH),
        help="Log file path (default: logs/scrape_fom.log)",
    )
    args = parser.parse_args()
    setup_logging(Path(args.log_file))
    log.info("Log file: %s", Path(args.log_file).resolve())
    county = (args.county or "").strip() or None

    try:
        result = run(
            headed=args.headed,
            county=county,
            timeout_ms=args.timeout,
            out_path=Path(args.out) if args.out else None,
        )
    except PlaywrightTimeout as exc:
        log.exception("Timed out waiting for the page: %s", exc)
        sys.exit(2)
    except Exception as exc:
        log.exception("Scrape failed: %s", exc)
        sys.exit(1)

    log.info("Saved %s deal links", len(result.get("links") or []))


if __name__ == "__main__":
    main()
