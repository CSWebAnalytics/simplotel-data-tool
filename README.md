# Simplotel Data Tool

Internal tool for downloading property data from the Simplotel admin panel.
Authentication uses your browser session cookie — no passwords are stored.

---

## What it downloads

| Section           | Output file              | Notes                          |
|-------------------|--------------------------|--------------------------------|
| Hotel Information | `hotel_info.csv`         | Name, address, contact, website |
| Room Information  | `room_info.csv` + others | Rooms transposed to rows; amenities matrix |
| Rate Plans        | `rate_plans.csv`         | All plan names from the panel  |
| Bookings          | `bookings.csv`           | PII columns stripped (name, email, contact) |
| Search Failures   | `search_failures.csv`    | From Analytics → Booking Engine Searches |

All files are bundled in a single ZIP: `simplotel_<property_id>.zip`

---

## Deploying on Railway (recommended — free tier works)

1. Create a free account at https://railway.app
2. Click **New Project → Deploy from GitHub repo** (or use the Railway CLI)
3. Upload / push this folder to a GitHub repo
4. Railway will auto-detect the `Dockerfile` and build
5. In the Railway dashboard, go to **Settings → Networking → Generate Domain**
6. Share that URL with your team (e.g. `simplotel-data-tool.up.railway.app`)

That's it — 20 people can use the same URL simultaneously.

---

## Deploying on Render (alternative)

1. Create a free account at https://render.com
2. New → **Web Service → Connect your repo**
3. Set:
   - **Environment**: Docker
   - **Port**: 8000
4. Click Deploy — Render will build from the Dockerfile
5. Share the Render URL with your team

---

## Running locally (for testing)

```bash
# Install dependencies
pip install -r requirements.txt
playwright install chromium

# Start the app
streamlit run streamlit_app.py
```

Streamlit will open http://localhost:8501 in your browser automatically.

---

## How team members get their session cookie

1. Log into admin.simplotel.com (complete OTP)
2. Open DevTools: `F12`
3. Go to **Network** tab → reload the page
4. Click any request to admin.simplotel.com
5. In **Request Headers**, find the `Cookie` row
6. Right-click → **Copy value**
7. Paste into the tool

Cookies expire when the browser session ends. If the tool reports
"invalid session", repeat these steps.

---

## Notes

- **Rate Plans**: This section scrapes an Angular SPA — results depend on
  how fast the page renders. If the CSV shows no plans, the Angular app
  may have been too slow. Re-run or increase the wait timeout in `scraper.py`
  (`_scrape_rate_plans`, line with `wait_for_timeout`).

- **Bookings export** uses Simplotel's own "Export To CSV" button —
  it's the most reliable section.

- **Search Failures** uses Simplotel's own "Export Search Failures To CSV"
  link — also reliable.

- The tool does not store cookies, property IDs, or any downloaded data.
  Each request is stateless.
