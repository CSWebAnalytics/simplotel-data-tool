"""
Simplotel Admin Panel Scraper — Synchronous Playwright version
Fixes Windows/Python 3.14 asyncio compatibility issues.
"""

import io
import re

import pandas as pd
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, Download

BASE_URL = "https://admin.simplotel.com"
BOOKINGS_PII_KEYWORDS = ["guest name", "email", "contact", "phone", "mobile"]
_SKIP_WORDS = {"filter", "create", "add new", "save", "cancel", "click", "export", "columns", "density"}


class SimplotelScraper:

    def __init__(self, property_id: str, cookie_string: str):
        self.property_id = property_id.strip()
        self.cookie_string = cookie_string.strip()
        self._playwright = None
        self.browser = None
        self.context = None
        self.page = None

    def init(self):
        self._playwright = sync_playwright().start()
        self.browser = self._playwright.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
        )
        self.context = self.browser.new_context(
            accept_downloads=True,
            viewport={"width": 1440, "height": 900},
        )
        cookies = self._parse_cookie_string(self.cookie_string)
        if cookies:
            self.context.add_cookies(cookies)
        self.page = self.context.new_page()

    def close(self):
        try:
            if self.browser:
                self.browser.close()
            if self._playwright:
                self._playwright.stop()
        except Exception:
            pass

    def _parse_cookie_string(self, cookie_string: str) -> list:
        cookies = []
        for part in cookie_string.split(";"):
            part = part.strip()
            if "=" in part:
                name, _, value = part.partition("=")
                cookies.append({
                    "name": name.strip(),
                    "value": value.strip(),
                    "domain": "admin.simplotel.com",
                    "path": "/",
                })
        return cookies

    def verify_session(self):
        url = f"{BASE_URL}/simp/{self.property_id}/hotelinfo/"
        self.page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        if "login" in self.page.url.lower():
            raise PermissionError(
                "Session cookie is invalid or expired. "
                "Please copy a fresh cookie from your browser and try again."
            )

    def scrape(self, pages: list, date_from=None, date_to=None, analytics_period=None) -> dict:
        self.init()
        self.verify_session()
        results = {}

        for key in pages:
            try:
                if key == "hotel_info":
                    results["hotel_info"] = self._scrape_hotel_info()
                elif key == "room_info":
                    results.update(self._scrape_room_info())
                elif key == "rate_plans":
                    results["rate_plans"] = self._scrape_rate_plans()
                elif key == "bookings":
                    results["bookings"] = self._scrape_bookings(date_from, date_to)
                elif key == "search_failures":
                    results["search_failures"] = self._scrape_search_failures(analytics_period, date_from, date_to)
            except Exception as exc:
                results[f"{key}_ERROR"] = f"Section,Error\n{key},{str(exc)}\n"

        return results

    def _scrape_hotel_info(self) -> str:
        self.page.goto(f"{BASE_URL}/simp/{self.property_id}/hotelinfo/", wait_until="networkidle", timeout=30_000)
        soup = BeautifulSoup(self.page.content(), "html.parser")
        data = {"property_id": self.property_id}

        for label in soup.find_all("label"):
            label_text = label.get_text(strip=True).rstrip(":").strip()
            if not label_text or len(label_text) > 80:
                continue
            for_attr = label.get("for")
            field = soup.find(id=for_attr) if for_attr else None
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

    def _scrape_room_info(self) -> dict:
        self.page.goto(f"{BASE_URL}/simp/{self.property_id}/room_info_settings/", wait_until="networkidle", timeout=30_000)
        try:
            self.page.wait_for_selector("table", timeout=15_000)
        except Exception:
            pass

        soup = BeautifulSoup(self.page.content(), "html.parser")
        tables = soup.find_all("table")
        section_names = ["room_info", "room_bed_view_config", "room_amenities"]
        results = {}

        for i, table in enumerate(tables[:5]):
            label = section_names[i] if i < len(section_names) else f"room_table_{i+1}"
            try:
                df = pd.read_html(str(table))[0]
                if i == 0 and len(df.columns) > 4:
                    df = df.set_index(df.columns[0]).T.reset_index()
                    df.rename(columns={"index": "Room Type"}, inplace=True)
                results[label] = df.to_csv(index=False)
            except Exception as exc:
                results[label] = f"Field,Error\n{label},{str(exc)}\n"

        return results if results else {"room_info": "No tables found.\n"}

    def _scrape_rate_plans(self) -> str:
        self.page.goto(f"{BASE_URL}/simp/{self.property_id}/bookingengine/simp/#/rateplans")
        self.page.wait_for_load_state("networkidle")
        self.page.wait_for_timeout(4_000)

        soup = BeautifulSoup(self.page.content(), "html.parser")
        seen, plans = set(), []

        for el in soup.select("ul li, .list-group-item"):
            _add_plan(el.get_text(strip=True), seen, plans)

        if not plans:
            for el in soup.find_all(["a", "span"], class_=re.compile(r"item|name|plan|rate", re.I)):
                _add_plan(el.get_text(strip=True), seen, plans)

        if not plans:
            plans = [{"Rate Plan Name": "Could not auto-extract — try re-running."}]

        return pd.DataFrame(plans).to_csv(index=False)

    def _scrape_bookings(self, date_from, date_to) -> str:
        url = (
            f"{BASE_URL}/simp/{self.property_id}/bookingengine/simp/"
            f"#/bookings?status=CONFIRMED%2CCLOSED%2CAMENDED&page=1&timezone=Asia%2FCalcutta"
        )
        if date_from:
            url += f"&booking_from={date_from}"
        if date_to:
            url += f"&booking_to={date_to}"

        self.page.goto(url)
        self.page.wait_for_load_state("networkidle")
        self.page.wait_for_timeout(4_000)

        try:
            self.page.wait_for_selector("table tbody tr", timeout=20_000)
        except Exception:
            self.page.wait_for_timeout(3_000)

        with self.page.expect_download(timeout=60_000) as dl_info:
            buttons = self.page.query_selector_all('a:text("Export To CSV"), button:text("Export To CSV")')
            if not buttons:
                raise RuntimeError("Export To CSV button not found.")
            buttons[0].click()

        tmp_path = dl_info.value.path()

        # Simplotel exports bookings as a ZIP containing the CSV
        import zipfile as zf
        with open(tmp_path, "rb") as fh:
            magic = fh.read(4)
        if magic[:2] == b'PK':
            with zf.ZipFile(tmp_path, "r") as z:
                csv_files = [n for n in z.namelist() if n.lower().endswith(".csv")]
                fname = csv_files[0] if csv_files else z.namelist()[0]
                raw_csv = z.read(fname).decode("utf-8-sig", errors="replace")
        else:
            with open(tmp_path, "r", encoding="utf-8-sig", errors="replace") as fh:
                raw_csv = fh.read()

        df = pd.read_csv(io.StringIO(raw_csv), on_bad_lines="skip")
        drop_cols = [c for c in df.columns if any(kw in c.lower() for kw in BOOKINGS_PII_KEYWORDS)]
        df.drop(columns=drop_cols, errors="ignore", inplace=True)
        return df.to_csv(index=False)

    def _scrape_search_failures(self, period, date_from, date_to) -> str:
        self.page.goto(f"{BASE_URL}/simp/{self.property_id}/bookingengine/simp/#/analytics")
        self.page.wait_for_load_state("networkidle")
        self.page.wait_for_timeout(6_000)

        period_labels = {"last_week": "Last Week", "last_month": "Last Month", "last_6_months": "Last 6 Months"}
        if period and period in period_labels:
            try:
                self.page.click(f"text={period_labels[period]}")
                self.page.wait_for_timeout(3_000)
            except Exception:
                pass
        elif date_from and date_to:
            try:
                self.page.click("text=Date Range")
                self.page.wait_for_timeout(1_000)
                date_inputs = self.page.query_selector_all('input[type="date"]')
                if len(date_inputs) >= 2:
                    date_inputs[-2].fill(str(date_from))
                    date_inputs[-1].fill(str(date_to))
                    self.page.wait_for_timeout(3_000)
            except Exception:
                pass

        with self.page.expect_download(timeout=60_000) as dl_info:
            self.page.click("text=Export Search Failures To CSV")

        tmp_path = dl_info.value.path()
        with open(tmp_path, "r", encoding="utf-8-sig", errors="replace") as fh:
            return fh.read()


def _add_plan(text: str, seen: set, plans: list):
    if not text or len(text) < 4 or len(text) > 150:
        return
    if text in seen:
        return
    if any(s in text.lower() for s in _SKIP_WORDS):
        return
    seen.add(text)
    plans.append({"Rate Plan Name": text})
