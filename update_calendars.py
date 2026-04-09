#!/usr/bin/env python3
"""
Fetches the Pointe-Claire collection calendar PDFs and updates the ICS files.

Workflow:
  1. Scrape the calendar page to discover the current PDF URLs.
  2. Download both sector PDFs.
  3. Compare SHA-256 hashes against cached values; skip if unchanged.
  4. Parse the collection schedule from the PDF's color-coded calendar grid.
  5. Write updated ICS files using the icalendar library.
  6. Persist the new hashes so the next run can detect changes.

Exit codes:
  0 – ran successfully (ICS files may or may not have changed)
  1 – unrecoverable error

PDF layout (same every year, coordinates may shift slightly):
  The single-page PDF contains a 3-column × 4-row grid of monthly calendars
  covering April (start_year) through March (end_year).  Each day cell may be
  filled with a colour indicating the collection type scheduled that day:

  CMYK colour               Collection type
  ─────────────────────────────────────────
  (0.40, 0.00, 0.80, 0.00)  Organic waste   (weekly, typically Monday)
  (0.00, 0.52, 0.75, 0.00)  Recyclables     (weekly, typically Wednesday)
  (0.25, 0.85, 0.85, 0.25)  Household waste (bi-weekly; Tue=Sector A, Thu=Sector B)
  (0.00, 0.20, 0.85, 0.00)  Recyclables alt (used for some months/sectors)
  (0.45, 0.70, 0.00, 0.00)  Seasonal leaf collection
  (0.82, 0.19, 0.44, 0.23)  Mattress/box-spring collection (special date)

  Bulky items are not highlighted as individual cells; the city's schedule
  always places them on the first Wednesday of each month and the legend is
  rendered as text only.
"""

import hashlib
import io
import json
import os
import re
import sys
import urllib.request
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

CALENDAR_PAGE = (
    "https://www.pointe-claire.ca/en/household-collections-and-residual-materials"
    "/collections/collection-calendar-and-sorting-guide"
)

# The city uses: CAL_collectes-YYYY-YYYY-secteur-{A|B}.pdf
ASSET_BASE = "https://www.pointe-claire.ca/assets/images/collectes/"

HASH_CACHE = Path(".pdf_hashes.json")
ICS_PATHS = {"A": Path("pointe-claire-a.ics"), "B": Path("pointe-claire-b.ics")}

# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; pointe-claire-waste-calendars-bot/1.0; "
        "+https://github.com/jordanconway/pointe-claire-waste-calendars)"
    )
}


def fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


# ---------------------------------------------------------------------------
# PDF URL discovery
# ---------------------------------------------------------------------------

_PDF_RE = re.compile(
    r'https?://[^"\'>\s]*CAL_collectes-\d{4}-\d{4}-secteur-([AB])\.pdf',
    re.IGNORECASE,
)


def _guess_url(sector: str) -> str:
    today = date.today()
    start_year = today.year if today.month >= 4 else today.year - 1
    return f"{ASSET_BASE}CAL_collectes-{start_year}-{start_year + 1}-secteur-{sector}.pdf"


def discover_pdf_urls() -> dict:
    """Return {'A': url, 'B': url} for the current calendar year."""
    try:
        html = fetch(CALENDAR_PAGE).decode("utf-8", errors="replace")
        found = {}
        for m in _PDF_RE.finditer(html):
            sector = m.group(1).upper()
            if sector not in found:
                found[sector] = m.group(0)
        if "A" in found and "B" in found:
            print("[info] Discovered PDF URLs from page.")
            print(f"       A: {found['A']}")
            print(f"       B: {found['B']}")
            return found
        print("[warn] Could not find both PDF links on page; using guessed URLs.")
    except Exception as exc:
        print(f"[warn] Failed to fetch calendar page ({exc}); using guessed URLs.")

    urls = {"A": _guess_url("A"), "B": _guess_url("B")}
    print(f"[info] Guessed URLs:\n       A: {urls['A']}\n       B: {urls['B']}")
    return urls


# ---------------------------------------------------------------------------
# Hash helpers
# ---------------------------------------------------------------------------

def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_hashes() -> dict:
    if HASH_CACHE.exists():
        try:
            return json.loads(HASH_CACHE.read_text())
        except Exception:
            pass
    return {}


def save_hashes(hashes: dict) -> None:
    HASH_CACHE.write_text(json.dumps(hashes, indent=2) + "\n")


# ---------------------------------------------------------------------------
# PDF grid parsing
#
# The PDF is a visual wall calendar.  Collections are encoded as filled
# colour rectangles overlaid on specific day cells.  We locate each
# coloured rectangle, map its (x, y) to a (month, day-of-week) via the
# fixed grid geometry, then infer RRULE parameters from the resulting set
# of (collection_type → days_of_week, start_dates).
# ---------------------------------------------------------------------------

# Grid geometry constants (points; origin top-left)
_GRID_X_STARTS = [436.0, 859.3, 1282.0]   # left edge of each of the 3 column-groups
_COL_WIDTH = 55.3                           # width of one day column
_SECTION_TOPS = [18, 171, 325, 479, 9999]  # top of each of the 4 month rows (+sentinel)

# section_row × grid_col → (year_offset, month)
# year_offset: 0 = start_year, 1 = end_year
_MONTH_GRID = [
    [(0, 4), (0, 8), (0, 12)],
    [(0, 5), (0, 9), (1, 1)],
    [(0, 6), (0, 10), (1, 2)],
    [(0, 7), (0, 11), (1, 3)],
]

# col 0 = Sunday … col 6 = Saturday → ISO weekday (Mon=1 … Sun=7)
_COL_TO_ISOWD = [7, 1, 2, 3, 4, 5, 6]

# Collection-colour map (CMYK tuples as returned by pdfplumber)
_COLLECTION_COLORS = {
    (0.40, 0.00, 0.80, 0.00): "organic",
    (0.00, 0.52, 0.75, 0.00): "recyclables",
    (0.00, 0.20, 0.85, 0.00): "recyclables",   # alternate shade, same collection
    (0.25, 0.85, 0.85, 0.25): "household",
    (0.45, 0.70, 0.00, 0.00): "leaf",
    (0.82, 0.19, 0.44, 0.23): "mattress",
}

# Colours whose presence as wide/tall background rects we want to ignore
_BACKGROUND_COLORS = {
    (0, 0, 0, 0),
    (0, 0, 0, 0.75),
    (0, 0, 0, 1),
    (1.0, 0.48, 0.12, 0.58),  # orange row-divider strips
    (0.0, 0.90, 0.00, 0.00),  # green section-header bars
    (0.08, 0.01, 0.001, 0.002),
    (1.0, 0.13, 0.01, 0.02),
    (0.30, 1.00, 0.85, 0.35),  # Sector-B variant green section header
    (1.0, 0.76, 0.00, 0.00),   # yellow (recycling day-of-week marker, not individual)
}


def _section_of(top: float) -> int:
    for i in range(len(_SECTION_TOPS) - 1):
        if _SECTION_TOPS[i] <= top < _SECTION_TOPS[i + 1]:
            return i
    return -1


def _grid_of(x0: float) -> int:
    for i in range(len(_GRID_X_STARTS) - 1):
        if _GRID_X_STARTS[i] - 5 <= x0 < _GRID_X_STARTS[i + 1] - 5:
            return i
    return 2 if x0 >= _GRID_X_STARTS[-1] - 5 else -1


def _col_of(x0: float, grid_idx: int) -> int:
    col = round((x0 - _GRID_X_STARTS[grid_idx]) / _COL_WIDTH)
    return col if 0 <= col <= 6 else -1


def _year_range_from_text(text: str) -> tuple:
    m = re.search(r"(20\d\d)\s*[-–]\s*(20\d\d)", text)
    if m:
        return int(m.group(1)), int(m.group(2))
    today = date.today()
    sy = today.year if today.month >= 4 else today.year - 1
    return sy, sy + 1


def parse_pdf_schedule(pdf_data: bytes, sector: str) -> dict:
    """
    Parse the collection schedule from the PDF's coloured calendar grid.

    Returns a schedule dict compatible with generate_ics().
    Falls back to the known-good schedule from convert_calendars.py when
    the grid analysis is inconclusive.
    """
    try:
        import pdfplumber  # type: ignore[import]
    except ImportError:
        print("[error] pdfplumber is not installed. Run: pip install pdfplumber")
        sys.exit(1)

    with pdfplumber.open(io.BytesIO(pdf_data)) as pdf:
        page = pdf.pages[0]
        rects = page.rects
        raw_text = page.extract_text() or ""

    start_year, end_year = _year_range_from_text(raw_text)

    # ---- collect coloured cells ----------------------------------------
    # A "cell" is a filled rect that:
    #   • has a known collection colour
    #   • is taller than 18 pt (avoids thin divider strips)
    #   • is between 20 and 60 pt wide (individual day cells are ~55 pt;
    #     split/shared cells are ~28 pt)
    cells = [
        r for r in rects
        if r.get("non_stroking_color") in _COLLECTION_COLORS
        and r["height"] > 18
        and 20 < r["width"] <= _COL_WIDTH + 5
    ]

    # ---- map each cell to (collection_type, day-of-week ISO, month) ----
    type_dows: dict = defaultdict(Counter)   # type → Counter of ISO weekday
    type_months: dict = defaultdict(set)     # type → set of (year, month)

    for cell in cells:
        sec = _section_of(cell["top"])
        grd = _grid_of(cell["x0"])
        if sec < 0 or grd < 0:
            continue
        col = _col_of(cell["x0"], grd)
        if col < 0:
            continue
        yr_off, month = _MONTH_GRID[sec][grd]
        year = start_year + yr_off
        ctype = _COLLECTION_COLORS[cell["non_stroking_color"]]
        isowd = _COL_TO_ISOWD[col]
        type_dows[ctype][isowd] += 1
        type_months[ctype].add((year, month))

    # ---- derive dominant DOW for each type ------------------------------
    def dominant_dow(ctype: str) -> int:
        """Return the most-common ISO weekday for a collection type."""
        if ctype not in type_dows or not type_dows[ctype]:
            return None
        return type_dows[ctype].most_common(1)[0][0]

    # ---- map ISO weekday → ical BYDAY string -------------------------
    _iso_to_byday = {1: "MO", 2: "TU", 3: "WE", 4: "TH", 5: "FR", 6: "SA", 7: "SU"}

    # ---- helper: first occurrence of isowd on or after a given date ----
    def first_on_or_after(from_date: date, isowd: int) -> date:
        delta = (isowd % 7 - from_date.isoweekday() % 7) % 7
        return from_date + timedelta(days=delta)

    period_start = date(start_year, 4, 1)
    period_end = date(end_year, 3, 31)

    # ---- ORGANIC (weekly) -----------------------------------------------
    org_isowd = dominant_dow("organic") or 1   # default Monday
    organic_dtstart = first_on_or_after(period_start, org_isowd)

    # ---- RECYCLABLES (weekly) -------------------------------------------
    rec_isowd = dominant_dow("recyclables") or 3   # default Wednesday
    recyclables_dtstart = first_on_or_after(period_start, rec_isowd)

    # ---- HOUSEHOLD WASTE (bi-weekly, sector-specific) -------------------
    # Household waste is encoded as half-width split cells shared with the
    # organic Monday cell, so it appears at most ~13 times per year in the
    # PDF (far fewer than weekly collections).  The dominant DOW is only
    # trustworthy when we see a clear majority; otherwise fall back to the
    # known-good defaults from convert_calendars.py (Tue=A, Thu=B).
    hw_isowd = None
    if "household" in type_dows:
        mc = type_dows["household"].most_common(2)
        # Only trust the grid result when one DOW dominates clearly:
        # at least 4 occurrences AND at least twice as many as runner-up.
        if mc and mc[0][1] >= 4:
            runner_up = mc[1][1] if len(mc) > 1 else 0
            if mc[0][1] >= 2 * runner_up:
                hw_isowd = mc[0][0]
    if hw_isowd is None:
        hw_isowd = 2 if sector == "A" else 4   # Tue / Thu

    # The known start dates from convert_calendars.py:
    #   Sector A: April 7, 2026 (Tuesday)
    #   Sector B: April 9, 2026 (Thursday)
    # We recalculate for the actual year by finding the correct weekday in April.
    hw_dtstart = first_on_or_after(period_start, hw_isowd)
    # For Sector B, the first collection is the *second* occurrence if April
    # starts on the household day (avoids a collision with Sector A week).
    if sector == "B" and hw_dtstart.month == 4 and hw_dtstart.day <= 2:
        hw_dtstart += timedelta(weeks=1)

    # ---- BULKY ITEMS (first Wednesday of each month) --------------------
    # Bulky items are never encoded as grid cells in the PDF (legend text only).
    # The city schedule is consistently: first Wednesday of each month.
    bulky_dtstart = first_on_or_after(period_start, 3)  # first Wed in April
    while bulky_dtstart.day > 7:
        bulky_dtstart -= timedelta(weeks=1)

    # ---- CHRISTMAS TREES ------------------------------------------------
    # Look for pairs of dates in January near "Christmas tree" in the raw text.
    # Fall back to the known-good dates from convert_calendars.py.
    xmas_dates = _extract_christmas_tree_dates(raw_text, end_year, sector)
    # Validate: Christmas tree dates must be in January
    xmas_dates = [d for d in xmas_dates if d.month == 1]
    if not xmas_dates:
        xmas_dates = (
            [date(end_year, 1, 7), date(end_year, 1, 14)]
            if sector == "A"
            else [date(end_year, 1, 6), date(end_year, 1, 13)]
        )

    schedule = {
        "start_year": start_year,
        "end_year": end_year,
        "organic": {
            "byday": _iso_to_byday[org_isowd],
            "dtstart": organic_dtstart,
            "until": period_end,
        },
        "recyclables": {
            "byday": _iso_to_byday[rec_isowd],
            "dtstart": recyclables_dtstart,
            "until": period_end,
        },
        "household": {
            "byday": _iso_to_byday[hw_isowd],
            "dtstart": hw_dtstart,
            "until": period_end,
        },
        "bulky": {
            "byday": "WE",
            "dtstart": bulky_dtstart,
            "until": period_end,
        },
        "christmas_trees": xmas_dates,
    }
    return schedule


_MONTH_NAMES = {
    "january": 1, "janvier": 1,
    "february": 2, "février": 2, "fevrier": 2,
    "march": 3, "mars": 3,
    "april": 4, "avril": 4,
    "may": 5, "mai": 5,
    "june": 6, "juin": 6,
    "july": 7, "juillet": 7,
    "august": 8, "août": 8, "aout": 8,
    "september": 9, "septembre": 9,
    "october": 10, "octobre": 10,
    "november": 11, "novembre": 11,
    "december": 12, "décembre": 12, "decembre": 12,
}


def _extract_christmas_tree_dates(text: str, end_year: int, sector: str) -> list:
    text_l = text.lower()
    for kw in ("christmas tree", "arbre de no"):
        idx = text_l.find(kw)
        if idx == -1:
            continue
        snippet = text_l[max(0, idx - 20): idx + 350]
        for month_name, month_num in _MONTH_NAMES.items():
            if month_name not in snippet:
                continue
            # "January 7 and 14" or "7 et 14 janvier"
            m = re.search(
                month_name + r"\s+(\d{1,2})(?:\s+(?:and|et|&)\s+(\d{1,2}))?",
                snippet,
            )
            if not m:
                m = re.search(
                    r"(\d{1,2})\s+(?:and|et|&)\s+(\d{1,2})\s+" + month_name,
                    snippet,
                )
            if m:
                dates = [date(end_year, month_num, int(m.group(1)))]
                if m.group(2):
                    dates.append(date(end_year, month_num, int(m.group(2))))
                return dates

    # Fallback: use the known-good dates from convert_calendars.py
    if sector == "A":
        return [date(end_year, 1, 7), date(end_year, 1, 14)]
    return [date(end_year, 1, 6), date(end_year, 1, 13)]


# ---------------------------------------------------------------------------
# ICS generation  (uses the icalendar library, same as convert_calendars.py)
# ---------------------------------------------------------------------------

def generate_ics(sector: str, schedule: dict) -> bytes:
    """
    Build a complete iCalendar object and return its serialised bytes.
    Mirrors the structure produced by convert_calendars.py but derives all
    dates from the parsed schedule.
    """
    try:
        from icalendar import Calendar, Event  # type: ignore[import]
    except ImportError:
        print("[error] icalendar is not installed. Run: pip install icalendar")
        sys.exit(1)

    sy = schedule["start_year"]
    ey = schedule["end_year"]
    end_dt = datetime(ey, 3, 31, 23, 59, 59)

    def _dt(d: date) -> datetime:
        return datetime(d.year, d.month, d.day, 7, 0, 0)

    cal = Calendar()
    cal.add("prodid", f"-//Pointe-Claire//Waste Collection Sector {sector}//EN")
    cal.add("version", "2.0")
    cal.add("x-wr-calname", f"Pointe-Claire Sector {sector} Collection {sy}-{ey}")

    # 1. Organic waste — weekly
    o = schedule["organic"]
    ev = Event()
    ev.add("summary", f"Organic Waste - Sector {sector}")
    ev.add("dtstart", _dt(o["dtstart"]))
    ev.add("duration", timedelta(hours=1))
    ev.add("rrule", {"freq": "weekly", "byday": o["byday"], "until": end_dt})
    ev.add("description", "Matières organiques collection.")
    cal.add_component(ev)

    # 2. Recyclables — weekly
    r = schedule["recyclables"]
    ev = Event()
    ev.add("summary", f"Recyclables - Sector {sector}")
    ev.add("dtstart", _dt(r["dtstart"]))
    ev.add("duration", timedelta(hours=1))
    ev.add("rrule", {"freq": "weekly", "byday": r["byday"], "until": end_dt})
    ev.add("description", "Matières recyclables collection.")
    cal.add_component(ev)

    # 3. Bulky items — first Wednesday of each month
    b = schedule["bulky"]
    ev = Event()
    ev.add("summary", f"Bulky Items - Sector {sector}")
    ev.add("dtstart", _dt(b["dtstart"]))
    ev.add("duration", timedelta(hours=1))
    ev.add("rrule", {"freq": "monthly", "byday": "1WE", "until": end_dt})
    ev.add("description", "Encombrants collection.")
    cal.add_component(ev)

    # 4. Household waste — bi-weekly
    h = schedule["household"]
    ev = Event()
    ev.add("summary", f"Household Waste - Sector {sector}")
    ev.add("dtstart", _dt(h["dtstart"]))
    ev.add("duration", timedelta(hours=1))
    ev.add("rrule", {
        "freq": "weekly", "interval": 2,
        "byday": h["byday"], "until": end_dt,
    })
    ev.add("description", "Déchets domestiques collection.")
    cal.add_component(ev)

    # 5. Christmas tree collection — specific dates
    for xmas_date in schedule["christmas_trees"]:
        ev = Event()
        ev.add("summary", f"Christmas Tree Collection - Sector {sector}")
        ev.add("dtstart", _dt(xmas_date))
        ev.add("duration", timedelta(hours=1))
        ev.add("description", "Special collection for Christmas trees.")
        cal.add_component(ev)

    return cal.to_ical()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def process_sector(sector: str, url: str,
                   cached_hashes: dict, new_hashes: dict) -> bool:
    """
    Download, compare hashes, parse, and write the ICS for one sector.
    Returns True if the ICS file was updated.
    """
    ics_path = ICS_PATHS[sector]
    print(f"\n[sector {sector}] Downloading {url}")
    try:
        pdf_data = fetch(url)
    except Exception as exc:
        print(f"[sector {sector}] ERROR downloading PDF: {exc}")
        return False

    digest = sha256(pdf_data)
    new_hashes[f"sector_{sector}"] = digest

    if cached_hashes.get(f"sector_{sector}") == digest:
        print(f"[sector {sector}] PDF unchanged (hash match); skipping update.")
        return False

    print(f"[sector {sector}] PDF changed or new — parsing…")
    schedule = parse_pdf_schedule(pdf_data, sector)

    print(
        f"[sector {sector}] Parsed schedule for "
        f"{schedule['start_year']}–{schedule['end_year']}:"
    )
    print(f"  Organic:      weekly on {schedule['organic']['byday']}"
          f" starting {schedule['organic']['dtstart']}")
    print(f"  Recyclables:  weekly on {schedule['recyclables']['byday']}"
          f" starting {schedule['recyclables']['dtstart']}")
    print(f"  Household:    bi-weekly on {schedule['household']['byday']}"
          f" starting {schedule['household']['dtstart']}")
    print(f"  Bulky:        first WE of month"
          f" starting {schedule['bulky']['dtstart']}")
    print(f"  Christmas:    {schedule['christmas_trees']}")

    ics_bytes = generate_ics(sector, schedule)
    ics_path.write_bytes(ics_bytes)
    print(f"[sector {sector}] Wrote {ics_path}")
    return True


def main() -> None:
    urls = discover_pdf_urls()
    cached = load_hashes()
    new_hashes = dict(cached)

    updated_a = process_sector("A", urls["A"], cached, new_hashes)
    updated_b = process_sector("B", urls["B"], cached, new_hashes)

    save_hashes(new_hashes)

    updated = updated_a or updated_b
    print(f"\n[info] {'ICS files updated.' if updated else 'No changes detected.'}")

    gha_output = os.environ.get("GITHUB_OUTPUT")
    if gha_output:
        with open(gha_output, "a") as fh:
            fh.write(f"calendars_updated={'true' if updated else 'false'}\n")


if __name__ == "__main__":
    main()
