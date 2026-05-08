"""
Simplotel Admin Panel Scraper
Uses Playwright (headless Chromium) with session cookies to extract data.
"""

import asyncio
import io
import os
import re
import tempfile
from typing import Optional

import pandas as pd
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright, Download

BASE_URL = "https://admin.simplotel.com"

# Columns to strip from bookings export (PII)
BOOKINGS_PII_KEYWORDS = ["guest name", "email", "contact", "phone", "mobile"]


class SimplotelScraper:

    def __init__(self, property_id: str, cookie_string: str):
        self.property_id = property_id.strip()
        self.cookie_string = cookie_string.strip()
        self._playwright = None
        self.browser = None
        self.context = None
        self.page = None

    # ──────────────────────────────────────────────────────────────────────────
    # Lifecycle
    # ──────────────────────────────────────────────────────────────────────────

    async def init(self):
        self._playwright = await async_playwright().start()
        self.browser = await self._playwright.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
        )
        self.context = await self.browser.new_context(
            accept_downloads=True,
            viewport={"width": 1440, "height": 900},
        )
        cookies = self._parse_cookie_string(self.cookie_string)
        if cookies:
            await self.context.add_cookies(cookies)
        self.page = await self.context.new_page()

    async def close(self):
        try:
            if self.browser:
                await self.browser.close()
            if self._playwright:
                await self._playwright.stop()
        except Exception:
            pass

    def _parse_cookie_string(self, cookie_string: str) -> list:
        """Parse `name=value; name2=value2` into Playwright cookie dicts."""
        cookies = []
        for part in cookie_string.split(";"):
            part = part.strip()
            if "=" in part:
                name, _, value = part.partition("=")
                cookies.append(
                    {
                        "name": name.strip(),
                        "value": value.strip(),
                        "domain": "admin.simplotel.com",
                        "path": "/",
                    }
                )
        return cookies

    # ──────────────────────────────────────────────────────────────────────────
    # Session validation
    # ──────────────────────────────────────────────────────────────────────────

    async def verify_session(self):
        """Navigate to hotel info page; raise if redirected to login."""
        url = f"{BASE_URL}/simp/{self.property_id}/hotelinfo/"
        await self.page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        if "login" in self.page.url.lower():
            raise PermissionError(
                "Session cookie is invalid or expired. "
                "Please copy a fresh cookie from your browser and try again."
            )

    # ──────────────────────────────────────────────────────────────────────────
    # Main entry point
    # ──────────────────────────────────────────────────────────────────────────

    async def scrape(
        self,
        pages: list,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        analytics_period: Optional[str] = None,
    ) -> dict:
        await self.init()
        await self.verify_session()

        results = {}

        dispatch = {
            "hotel_info":       self._scrape_hotel_info,
            "room_info":        self._scrape_room_info,
            "rate_plans":       self._scrape_rate_plans,
            "bookings":         lambda: self._scrape_bookings(date_from, date_to),
            "search_failures":  lambda: self._scrape_search_failures(analytics_period, date_from, date_to),
        }

        for key in pages:
            if key not in dispatch:
                continue
            try:
                outcome = await dispatch[key]()
                if isinstance(outcome, dict):
                    results.update(outcome)
                else:
                    results[key] = outcome
            except Exception as exc:
                # Store error as a one-row CSV so the ZIP still contains something
                results[f"{key}_ERROR"] = f"Section,Error\n{key},{str(exc)}\n"

        return results

    # ──────────────────────────────────────────────────────────────────────────
    # 1. Hotel Information
    # ──────────────────────────────────────────────────────────────────────────

    async def _scrape_hotel_info(self) -> str:
        url = f"{BASE_URL}/simp/{self.property_id}/hotelinfo/"
        await self.page.goto(url, wait_until="networkidle", timeout=30_000)
        soup = BeautifulSoup(await self.page.content(), "html.parser")

        data = {"property_id": self.property_id}

        # Walk every <label> → find its associated field
        for label in soup.find_all("label"):
            label_text = label.get_text(strip=True).rstrip(":").strip()
            if not label_text or len(label_text) > 80:
                continue

            for_attr = label.get("for")
            field = soup.find(id=for_attr) if for_attr else None

            # Fallback: sibling input
            if not field:
                field = label.find_next_sibling(["input", "textarea", "select"])

            if not field:
                continue

            if field.name == "input":
                data[label_text] = field.get("value", "")
            elif field.name == "textarea":
                data[label_text] = field.get_text(strip=True)
            elif field.name == "select":
                selected = field.find("option", selected=True)
                data[label_text] = selected.get_text(strip=True) if selected else ""

        return pd.DataFrame([data]).to_csv(index=False)

    # ──────────────────────────────────────────────────────────────────────────
    # 2. Room Information
    # ──────────────────────────────────────────────────────────────────────────

    async def _scrape_room_info(self) -> dict:
        url = f"{BASE_URL}/simp/{self.property_id}/room_info_settings/"
        await self.page.goto(url, wait_until="networkidle", timeout=30_000)
        try:
            await self.page.wait_for_selector("table", timeout=15_000)
        except Exception:
            pass

        soup = BeautifulSoup(await self.page.content(), "html.parser")
        tables = soup.find_all("table")

        section_names = [
            "room_info",
            "room_bed_view_config",
            "room_amenities",
        ]

        results = {}
        for i, table in enumerate(tables[:5]):
            label = section_names[i] if i < len(section_names) else f"room_table_{i+1}"
            try:
                df = pd.read_html(str(table))[0]
                # Room Information table has room types as columns — transpose so
                # each row represents one room type.
                if i == 0 and len(df.columns) > 4:
                    first_col = df.columns[0]
                    df = df.set_index(first_col).T.reset_index()
                    df.rename(columns={"index": "Room Type"}, inplace=True)
                results[label] = df.to_csv(index=False)
            except Exception as exc:
                results[label] = f"Field,Error\n{label},{str(exc)}\n"

        return results if results else {"room_info": "No tables found on room info page.\n"}

    # ──────────────────────────────────────────────────────────────────────────
    # 3. Rate Plans  (Angular SPA)
    # ──────────────────────────────────────────────────────────────────────────

    async def _scrape_rate_plans(self) -> str:
        url = f"{BASE_URL}/simp/{self.property_id}/bookingengine/simp/#/rateplans"
        await self.page.goto(url)
        await self.page.wait_for_load_state("networkidle")
        await self.page.wait_for_timeout(4_000)  # Angular render

        soup = BeautifulSoup(await self.page.content(), "html.parser")

        seen: set = set()
        plans: list = []

        # Try sidebar list items first
        for el in soup.select("ul li, .list-group-item, [class*='plan'] li, [class*='rate'] li"):
            text = el.get_text(strip=True)
            _add_plan(text, seen, plans)

        # Fallback: any anchor / span that looks like a plan name in the left panel
        if not plans:
            sidebar_candidates = soup.find_all(
                ["a", "span", "div"],
                class_=re.compile(r"item|name|plan|rate", re.I),
            )
            for el in sidebar_candidates:
                text = el.get_text(strip=True)
                _add_plan(text, seen, plans)

        if not plans:
            plans = [
                {
                    "Rate Plan Name": "Could not auto-extract. "
                    "Angular may still be loading — try re-running or check the page manually."
                }
            ]

        return pd.DataFrame(plans).to_csv(index=False)

    # ──────────────────────────────────────────────────────────────────────────
    # 4. Bookings  (click Export To CSV, strip PII)
    # ──────────────────────────────────────────────────────────────────────────

    async def _scrape_bookings(
        self, date_from: Optional[str], date_to: Optional[str]
    ) -> str:
        url = (
            f"{BASE_URL}/simp/{self.property_id}/bookingengine/simp/"
            f"#/bookings?status=CONFIRMED%2CCLOSED%2CAMENDED&page=1&timezone=Asia%2FCalcutta"
        )
        if date_from:
            url += f"&booking_from={date_from}"
        if date_to:
            url += f"&booking_to={date_to}"

        await self.page.goto(url)
        await self.page.wait_for_load_state("networkidle")
        await self.page.wait_for_timeout(4_000)

        # Wait for at least one table row
        try:
            await self.page.wait_for_selector("table tbody tr", timeout=20_000)
        except Exception:
            await self.page.wait_for_timeout(3_000)

        # Click the first "Export To CSV" button (not the "For All Properties" one)
        async with self.page.expect_download(timeout=60_000) as dl_info:
            buttons = await self.page.query_selector_all(
                'a:text("Export To CSV"), button:text("Export To CSV")'
            )
            if not buttons:
                raise RuntimeError(
                    "Export To CSV button not found on bookings page. "
                    "The page may not have loaded or the cookie may be expired."
                )
            await buttons[0].click()

        download: Download = await dl_info.value
        tmp_path = await download.path()
        with open(tmp_path, "r", encoding="utf-8-sig", errors="replace") as fh:
            raw_csv = fh.read()

        # Remove PII columns
        df = pd.read_csv(io.StringIO(raw_csv))
        drop_cols = [
            c for c in df.columns
            if any(kw in c.lower() for kw in BOOKINGS_PII_KEYWORDS)
        ]
        df.drop(columns=drop_cols, errors="ignore", inplace=True)

        return df.to_csv(index=False)

    # ──────────────────────────────────────────────────────────────────────────
    # 5. Search Failures from Analytics
    # ──────────────────────────────────────────────────────────────────────────

    async def _scrape_search_failures(
        self,
        period: Optional[str],
        date_from: Optional[str],
        date_to: Optional[str],
    ) -> str:
        url = f"{BASE_URL}/simp/{self.property_id}/bookingengine/simp/#/analytics"
        await self.page.goto(url)
        await self.page.wait_for_load_state("networkidle")
        await self.page.wait_for_timeout(6_000)  # Analytics dashboard is heavy

        # Apply period preset
        period_labels = {
            "last_week":    "Last Week",
            "last_month":   "Last Month",
            "last_6_months":"Last 6 Months",
        }
        if period and period in period_labels:
            try:
                await self.page.click(f"text={period_labels[period]}")
                await self.page.wait_for_timeout(3_000)
            except Exception:
                pass
        elif date_from and date_to:
            try:
                await self.page.click("text=Date Range")
                await self.page.wait_for_timeout(1_000)
                date_inputs = await self.page.query_selector_all('input[type="date"]')
                if len(date_inputs) >= 2:
                    await date_inputs[-2].fill(date_from)
                    await date_inputs[-1].fill(date_to)
                    # Some implementations require clicking a Send/Apply button
                    try:
                        await self.page.click("text=Send Report")
                    except Exception:
                        await self.page.keyboard.press("Enter")
                    await self.page.wait_for_timeout(3_000)
            except Exception:
                pass

        # Click Export Search Failures To CSV
        async with self.page.expect_download(timeout=60_000) as dl_info:
            try:
                await self.page.click("text=Export Search Failures To CSV")
            except Exception:
                raise RuntimeError(
                    "Could not find 'Export Search Failures To CSV' link. "
                    "The analytics section may still be loading."
                )

        download: Download = await dl_info.value
        tmp_path = await download.path()
        with open(tmp_path, "r", encoding="utf-8-sig", errors="replace") as fh:
            return fh.read()


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

_SKIP_WORDS = {"filter", "create", "add new", "save", "cancel", "click", "export", "columns", "density"}


def _add_plan(text: str, seen: set, plans: list):
    if not text or len(text) < 4 or len(text) > 150:
        return
    if text in seen:
        return
    if any(s in text.lower() for s in _SKIP_WORDS):
        return
    seen.add(text)
    plans.append({"Rate Plan Name": text})
