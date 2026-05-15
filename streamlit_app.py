"""
Simplotel Data Tool — Streamlit frontend
"""

import io
import subprocess
import sys
import zipfile
from datetime import date

import streamlit as st

from scraper import SimplotelScraper


# ── Install Playwright Chromium on first run (Streamlit Cloud) ─────────────────
@st.cache_resource(show_spinner="Setting up browser (first run only)…")
def install_playwright():
    subprocess.run(
        [sys.executable, "-m", "playwright", "install", "chromium"],
        check=True, capture_output=True
    )

install_playwright()

# ── Page config ────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Simplotel Data Tool",
    page_icon="🏨",
    layout="centered",
)

# ── Header ─────────────────────────────────────────────────────────────────────
st.title("🏨 Simplotel Data Tool")
st.caption("Internal use only — extracts property data from the Simplotel admin panel.")
st.divider()


# ── Helper: run async scraper from sync Streamlit ─────────────────────────────
def run_scraper(property_id, cookie_string, pages, date_from, date_to, analytics_period):
    scraper = SimplotelScraper(property_id=property_id, cookie_string=cookie_string)
    try:
        results = scraper.scrape(
            pages=pages,
            date_from=str(date_from) if date_from else None,
            date_to=str(date_to) if date_to else None,
            analytics_period=analytics_period,
        )
    finally:
        scraper.close()
    return results


def build_zip(results: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in results.items():
            if isinstance(content, str):
                zf.writestr(f"{name}.csv", content.encode("utf-8"))
            else:
                zf.writestr(f"{name}.csv", content)
    return buf.getvalue()


# ── Step 1: Property ID ────────────────────────────────────────────────────────
st.subheader("Step 1 — Property")
property_id = st.text_input("Property ID", placeholder="e.g. 11550", max_chars=20)

# ── Step 2: Session Cookie ─────────────────────────────────────────────────────
st.subheader("Step 2 — Session Cookie")

with st.expander("📋 How to get your session cookie (30 seconds)", expanded=False):
    st.markdown("""
1. Log into **admin.simplotel.com** in Chrome (complete the OTP step).
2. Open DevTools: `F12` → go to the **Network** tab.
3. Reload the page.
4. Click any request to `admin.simplotel.com`.
5. In **Request Headers**, find the **Cookie** row.
6. Right-click the value → **Copy value**.
7. Paste it below.

> **Note:** Your cookie expires when your browser session ends.  
> If the tool says "Invalid session", repeat these steps to get a fresh one.
    """)

cookie_string = st.text_area(
    "Paste your browser cookie string",
    placeholder="sessionid=abc123; csrftoken=xyz456; ...",
    height=90,
    label_visibility="visible",
)

# ── Step 3: Data sections ──────────────────────────────────────────────────────
st.subheader("Step 3 — Select Data Sections")

col1, col2 = st.columns(2)
with col1:
    hotel_info    = st.checkbox("🏷️ Hotel Information",  help="Name, address, website, contacts")
    room_info     = st.checkbox("🛏️ Room Information",   help="Room types, counts, sizes, amenities")
    rate_plans    = st.checkbox("💳 Rate Plans",          help="All rate plan names and configs")
with col2:
    bookings      = st.checkbox("📋 Bookings",            help="Confirmed / Closed / Amended — no guest PII")
    search_failures = st.checkbox("🔍 Search Failures",  help="Failed booking engine searches (Analytics)")

selected_pages = []
if hotel_info:      selected_pages.append("hotel_info")
if room_info:       selected_pages.append("room_info")
if rate_plans:      selected_pages.append("rate_plans")
if bookings:        selected_pages.append("bookings")
if search_failures: selected_pages.append("search_failures")

# ── Date range (shown when Bookings or Search Failures selected) ───────────────
date_from = date_to = None
analytics_period = "current"

if bookings or search_failures:
    st.divider()

if bookings:
    st.markdown("**📅 Bookings Date Range** *(leave blank for all time)*")
    c1, c2 = st.columns(2)
    with c1:
        date_from = st.date_input("From", value=None, key="date_from")
    with c2:
        date_to   = st.date_input("To",   value=date.today(), key="date_to")

if search_failures:
    st.markdown("**📊 Analytics Period**")
    period_map = {
        "Ongoing Month":  "current",
        "Last Week":      "last_week",
        "Last Month":     "last_month",
        "Last 6 Months":  "last_6_months",
    }
    period_label   = st.radio("Select period", list(period_map.keys()), horizontal=True, label_visibility="collapsed")
    analytics_period = period_map[period_label]

# ── Step 4: Run ────────────────────────────────────────────────────────────────
st.subheader("Step 4 — Download")
st.divider()

run_btn = st.button("⬇️ Extract & Download Data", type="primary", use_container_width=True)

if run_btn:
    # Validate
    if not property_id.strip():
        st.error("Please enter a Property ID.")
        st.stop()
    if not cookie_string.strip():
        st.error("Please paste your session cookie.")
        st.stop()
    if not selected_pages:
        st.error("Select at least one data section.")
        st.stop()

    with st.status("Extracting data… this may take 30–90 seconds.", expanded=True) as status:
        section_labels = {
            "hotel_info":      "Hotel Information",
            "room_info":       "Room Information",
            "rate_plans":      "Rate Plans",
            "bookings":        "Bookings",
            "search_failures": "Search Failures",
        }
        for p in selected_pages:
            st.write(f"⏳ Queued: **{section_labels.get(p, p)}**")

        try:
            results = run_scraper(
                property_id=property_id.strip(),
                cookie_string=cookie_string.strip(),
                pages=selected_pages,
                date_from=date_from,
                date_to=date_to,
                analytics_period=analytics_period,
            )

            # Report results
            errors = {k: v for k, v in results.items() if "ERROR" in k.upper()}
            ok     = {k: v for k, v in results.items() if "ERROR" not in k.upper()}

            for k in ok:
                st.write(f"✅ Done: **{section_labels.get(k.lstrip('0123456789_'), k)}**")
            for k in errors:
                st.write(f"❌ Failed: **{k}**")

            if ok:
                status.update(label="✅ Extraction complete!", state="complete", expanded=False)
            else:
                status.update(label="❌ All sections failed.", state="error", expanded=True)
                st.stop()

        except PermissionError as e:
            status.update(label="❌ Authentication failed.", state="error")
            st.error(str(e))
            st.stop()
        except Exception as e:
            status.update(label="❌ Extraction failed.", state="error")
            st.error(f"Error: {e}")
            st.stop()

    # Build ZIP and show download button
    zip_bytes  = build_zip(results)
    zip_name   = f"simplotel_{property_id.strip()}.zip"
    file_count = len([k for k in results if "ERROR" not in k.upper()])

    st.success(f"✅ {file_count} file(s) ready.")
    st.download_button(
        label=f"📥 Download {zip_name}",
        data=zip_bytes,
        file_name=zip_name,
        mime="application/zip",
        use_container_width=True,
    )

# ── Footer ─────────────────────────────────────────────────────────────────────
st.divider()
st.caption("Simplotel Data Tool • Internal • Data is not stored by this tool.")
