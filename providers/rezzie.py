"""Authenticated Rezzie buyer-dashboard provider."""

from __future__ import annotations

import os
import re
import json
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

from dotenv import load_dotenv
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from .base import ScraperProvider


class RezzieProvider(ScraperProvider):
    name = "rezzie"
    base_url = "https://rezzie.com"
    session_filename = "rezzie.json"
    dashboard_path = "/buyer/dashboard"
    listing_href = re.compile(r"/(?:buyer/)?(?:property|properties|listing|listings|deal|deals)/([^/?#]+)", re.I)
    price_re = re.compile(r"\$\s*[\d,]+(?:\.\d{2})?")
    number_re = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?")

    @staticmethod
    def _visible(locator: Any) -> bool:
        try:
            return locator.count() > 0 and locator.first.is_visible()
        except Exception:
            return False

    def _login_visible(self, page: Any) -> bool:
        if "/login" in (page.url or "").lower() or self._visible(page.get_by_label(re.compile(r"email", re.I))):
            return True
        # Rezzie protects the dashboard with an in-page auth guard instead of
        # always redirecting to /login.
        return self._visible(page.get_by_text(re.compile(r"authentication required", re.I)))

    def _credentials(self) -> tuple[str, str]:
        load_dotenv(Path(__file__).resolve().parents[1] / ".env")
        email, password = (os.getenv(key, "").strip() for key in ("REZZIE_EMAIL", "REZZIE_PASSWORD"))
        if not email or not password:
            raise RuntimeError("REZZIE_EMAIL and REZZIE_PASSWORD must be set in scraper/.env")
        return email, password

    def authenticate(self, page: Any, timeout_ms: int) -> None:
        if self.dashboard_path not in (page.url or ""):
            page.goto(urljoin(self.base_url, self.dashboard_path), wait_until="domcontentloaded", timeout=timeout_ms)
        # A restored storage state should get straight through to the dashboard.
        if not self._login_visible(page):
            return
        email, password = self._credentials()
        email_input = page.get_by_label(re.compile(r"email", re.I))
        if not self._visible(email_input):
            # Rezzie presents authentication as a modal from the protected
            # dashboard instead of navigating to a dedicated /login URL.
            sign_in = page.get_by_role("button", name=re.compile(r"^sign\s*in$", re.I))
            if self._visible(sign_in):
                sign_in.first.click()
            else:
                login = page.get_by_role("link", name=re.compile(r"log\s*in|sign\s*in", re.I))
                if not self._visible(login):
                    raise RuntimeError("Rezzie requires sign-in but no sign-in control was found")
                login.first.click()
            email_input.wait_for(state="visible", timeout=timeout_ms)
        email_input.first.fill(email)
        page.get_by_label(re.compile(r"password", re.I)).first.fill(password)
        form = page.locator("form").last
        submit = form.get_by_role("button", name=re.compile(r"log\s*in|sign\s*in|continue", re.I))
        if not self._visible(submit):
            submit = form.locator("button[type='submit']")
        if not self._visible(submit):
            # The guard's initial button remains in the DOM behind the modal;
            # the modal submit is the last matching action.
            submit = page.get_by_role("button", name=re.compile(r"log\s*in|sign\s*in|continue", re.I)).last
        submit.first.click()
        try:
            page.get_by_text(re.compile(r"authentication required", re.I)).wait_for(state="hidden", timeout=timeout_ms)
        except PlaywrightTimeout as exc:
            raise RuntimeError("Rezzie login did not complete; check credentials or run headed for MFA/CAPTCHA") from exc
        page.goto(urljoin(self.base_url, self.dashboard_path), wait_until="domcontentloaded", timeout=timeout_ms)
        if self._login_visible(page):
            raise RuntimeError("Rezzie redirected back to login; the account may require MFA or approval")

    @staticmethod
    def _clean_url(href: str, base_url: str) -> str:
        parts = urlsplit(urljoin(base_url, href))
        return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), "", ""))

    def collect_listings(self, page: Any, timeout_ms: int, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        page.goto(urljoin(self.base_url, self.dashboard_path), wait_until="domcontentloaded", timeout=timeout_ms)
        # The buyer dashboard is app-rendered; cards can arrive after the
        # initial document event. Do not race the frontend's API requests.
        try:
            page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 15000))
        except PlaywrightTimeout:
            pass
        self._write_debug_snapshot(page)
        results: dict[str, dict[str, Any]] = {}
        # Rezzie's current dashboard uses JavaScript buttons for its property
        # cards. Keep support for ordinary links, but use the buttons when the
        # app has not rendered any property anchors.
        detail_buttons = page.get_by_role("button", name=re.compile(r"^details$", re.I))
        if detail_buttons.count():
            return self._collect_button_listings(page, timeout_ms, filters)
        visited: set[str] = set()
        while page.url not in visited:
            visited.add(page.url)
            for anchor in page.locator("a[href]").all():
                href = anchor.get_attribute("href") or ""
                match = self.listing_href.search(href)
                if not match:
                    continue
                url = self._clean_url(href, self.base_url)
                if url in results:
                    continue
                text = " ".join((anchor.inner_text(timeout=2000) or "").split())
                if filters and any(filters.get(k) and str(filters[k]).lower() not in text.lower() for k in ("city", "county", "state")):
                    continue
                price = self.price_re.search(text)
                results[url] = {"listing_id": match.group(1), "url": url, "price": price.group(0) if price else None, "card_text": text}
            next_link = page.get_by_role("link", name=re.compile(r"next", re.I)).last
            next_href = next_link.get_attribute("href") if self._visible(next_link) else ""
            # Keep pagination query parameters; `_clean_url` intentionally removes
            # them from listing URLs so the same property is deduplicated.
            next_url = urljoin(page.url, next_href) if next_href else ""
            if not next_url or next_url in visited or (next_link.get_attribute("aria-disabled") or "").lower() == "true":
                break
            page.goto(next_url, wait_until="domcontentloaded", timeout=timeout_ms)
        return list(results.values())

    def _collect_button_listings(
        self,
        page: Any,
        timeout_ms: int,
        filters: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        """Resolve property routes by following Rezzie's dashboard buttons."""
        results: dict[str, dict[str, Any]] = {}
        seen_pages: set[str] = set()
        while True:
            body = page.locator("body").inner_text()
            page_marker = re.search(r"Page\s+(\d+)\s+of\s+(\d+)", body, re.I)
            marker = page_marker.group(0) if page_marker else f"page-{len(seen_pages) + 1}"
            if marker in seen_pages:
                break
            seen_pages.add(marker)

            button_count = page.get_by_role("button", name=re.compile(r"^details$", re.I)).count()
            for index in range(button_count):
                button = page.get_by_role("button", name=re.compile(r"^details$", re.I)).nth(index)
                # The card body is three levels above the Details button.
                card_text = " ".join(button.locator("xpath=../../..").inner_text().split())
                if filters and any(filters.get(key) and str(filters[key]).lower() not in card_text.lower() for key in ("city", "county", "state")):
                    continue
                button.click()
                try:
                    page.wait_for_url(lambda url: bool(self.listing_href.search(url)), timeout=timeout_ms)
                except PlaywrightTimeout as exc:
                    raise RuntimeError("Rezzie Details button did not open a property page") from exc
                url = self._clean_url(page.url, self.base_url)
                match = self.listing_href.search(url)
                if match and url not in results:
                    price = self.price_re.search(card_text)
                    results[url] = {
                        "listing_id": match.group(1),
                        "url": url,
                        "price": price.group(0) if price else None,
                        "card_text": card_text,
                    }
                page.go_back(wait_until="domcontentloaded", timeout=timeout_ms)
                try:
                    page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 10000))
                except PlaywrightTimeout:
                    pass

            next_button = page.get_by_role("button", name=re.compile(r"^next", re.I))
            if not self._visible(next_button) or next_button.first.is_disabled():
                break
            next_button.first.click()
            try:
                page.wait_for_function(
                    "previous => !document.body.innerText.includes(previous)",
                    arg=marker,
                    timeout=timeout_ms,
                )
            except PlaywrightTimeout:
                break
        return list(results.values())

    @staticmethod
    def _write_debug_snapshot(page: Any) -> None:
        """Write safe dashboard navigation diagnostics when explicitly enabled."""
        destination = (os.getenv("REZZIE_DEBUG_PATH") or "").strip()
        if not destination:
            return
        hrefs = []
        for anchor in page.locator("a[href]").all():
            href = (anchor.get_attribute("href") or "").strip()
            if href and href not in hrefs:
                hrefs.append(href)
        path = Path(destination)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            body_text = page.locator("body").inner_text(timeout=3000)[:6000]
        except Exception:
            body_text = ""
        path.write_text(
            json.dumps(
                {
                    "url": page.url,
                    "title": page.title(),
                    "anchor_count": len(hrefs),
                    "hrefs": hrefs,
                    "body_text": body_text,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        page.screenshot(path=str(path.with_suffix(".png")), full_page=True)

    @classmethod
    def _value(cls, text: str, labels: tuple[str, ...]) -> str | None:
        match = re.search(r"(?:" + "|".join(re.escape(x) for x in labels) + r")\s*[:\-]?\s*([^\n|]+)", text, re.I)
        return " ".join(match.group(1).split()).strip() if match else None

    @classmethod
    def _number(cls, value: str | None) -> int | float | None:
        match = cls.number_re.search(value or "")
        if not match:
            return None
        number = float(match.group(0).replace(",", ""))
        return int(number) if number.is_integer() else number

    @staticmethod
    def _section(lines: list[str], heading: str, stop_headings: tuple[str, ...]) -> list[str]:
        try:
            start = next(index for index, line in enumerate(lines) if line.lower() == heading.lower()) + 1
        except StopIteration:
            return []
        stop = len(lines)
        for index in range(start, len(lines)):
            if lines[index].lower() in {item.lower() for item in stop_headings}:
                stop = index
                break
        return lines[start:stop]

    @staticmethod
    def _adjacent_line(lines: list[str], label: str, *, before: bool = False) -> str | None:
        for index, line in enumerate(lines):
            if line.lower() != label.lower():
                continue
            candidate_index = index - 1 if before else index + 1
            if 0 <= candidate_index < len(lines):
                return lines[candidate_index]
        return None

    def extract_listing(self, page: Any, listing: dict[str, Any], timeout_ms: int) -> dict[str, Any]:
        page.goto(listing["url"], wait_until="domcontentloaded", timeout=timeout_ms)
        try:
            page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 15000))
        except PlaywrightTimeout:
            pass
        if self._login_visible(page):
            raise RuntimeError("Rezzie redirected to authentication while opening a property")
        try:
            page.locator("main").wait_for(state="visible", timeout=timeout_ms)
            text = page.locator("main").inner_text()
        except Exception:
            text = page.inner_text("body")
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        text = "\n".join(lines)
        title = (page.locator("h1").first.inner_text() or "").strip() if page.locator("h1").count() else None
        price = self.price_re.search(text)
        images = list(dict.fromkeys(urljoin(page.url, (img.get_attribute("src") or "").strip()) for img in page.locator("img").all() if (img.get_attribute("src") or "").strip() and not (img.get_attribute("src") or "").startswith("data:")))
        description_lines = self._section(lines, "Description", ("Property Intelligence", "Contact Seller", "Disclaimer:"))
        features = self._section(lines, "Available Features", ("Property Condition Details", "Rehab", "Access Instructions", "Property Details"))
        condition_lines = self._section(lines, "Property Condition Details", ("Rehab", "Access Instructions", "Property Details"))
        conditions = {
            condition_lines[index].rstrip(":"): condition_lines[index + 1]
            for index in range(0, len(condition_lines) - 1, 2)
        }
        contact = self._section(lines, "Contact Seller", ("Seller Suggestions", "Disclaimer:"))
        created = re.search(r"Created:\s*(.+)", text, re.I)
        updated = re.search(r"Updated:\s*(.+)", text, re.I)
        result: dict[str, Any] = {
            "listing_id": listing.get("listing_id"),
            "url": page.url or listing["url"],
            "title": title or None,
            "address": title or None,
            "price": price.group(0) if price else listing.get("price"),
            "purchase_price": self._adjacent_line(lines, "Purchase Price"),
            "estimated_rehab": self._adjacent_line(lines, "Estimated Rehab"),
            "total_investment": self._adjacent_line(lines, "Total Investment"),
            "estimated_arv": self._adjacent_line(lines, "ARV"),
            "deal_type": self._adjacent_line(lines, "Deal Type"),
            "price_per_sqft": self._adjacent_line(lines, "Price per Sq Ft"),
            "closing_date": self._adjacent_line(lines, "Closing Date"),
            "beds": self._number(self._adjacent_line(lines, "Bedrooms", before=True)),
            "baths": self._number(self._adjacent_line(lines, "Bathrooms", before=True)),
            "sqft": self._number(self._adjacent_line(lines, "Sq Ft", before=True)),
            "year_built": self._number(self._adjacent_line(lines, "Year Built", before=True)),
            "lot_sqft": self._number(self._value(text, ("Lot Size", "Lot Sq Ft"))),
            "property_type": self._value(text, ("Property Type",)),
            "construction": self._value(text, ("Construction",)),
            "property_condition": self._value(text, ("Condition",)),
            "description": "\n".join(description_lines) or None,
            "features": features,
            "condition_details": conditions,
            "seller_name": contact[0] if contact else None,
            "seller_role": contact[1] if len(contact) > 1 else None,
            "seller_company": contact[2] if len(contact) > 2 else None,
            "seller_email": next((item for item in contact if "@" in item), None),
            "seller_phone": next((item for item in contact if re.search(r"\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}", item)), None),
            "created_at": created.group(1).strip() if created else None,
            "updated_at": updated.group(1).strip() if updated else None,
            "images": images,
            "photo_count": len(images) or None,
            "raw_text": text,
            "error": None,
        }
        return result
