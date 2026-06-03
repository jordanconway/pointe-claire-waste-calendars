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
    (0.40, 0.00, 0.80, 0.00): "organic",      # Green (Apple core)
    (1.00, 0.76, 0.00, 0.00): "recyclables",  # Blue (Blue bin)
    (0.00, 0.00, 0.00, 0.75): "household",    # Grey (Trash can)
    (0.45, 0.70, 0.00, 0.00): "leaf",         # Brown (Leaf collection)
    (0.25, 0.85, 0.85, 0.25): "leaf",         # Brown (Alternative leaf collection shade)
    (0.82, 0.19, 0.44, 0.23): "mattress",     # Pink (Mattress/box-spring)
    (0.00, 0.52, 0.75, 0.00): "bulky",        # Orange (Bulky items)
    (0.00, 0.20, 0.85, 0.00): "ecocentre",    # Yellow-Orange (Ecocentre/HHW)
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

    Returns a schedule dict containing sorted lists of exact dates for each collection type,
    plus start_year and end_year.
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

    # Initialize collections
    collections = {k: set() for k in set(_COLLECTION_COLORS.values())}

    for r in rects:
        c = r.get("non_stroking_color")
        if c in _COLLECTION_COLORS:
            # Skip background and header blocks (anything too wide)
            if r["width"] > 56:
                continue
            # Height filter (skip line dividers)
            if r["height"] < 15:
                continue

            x0 = r["x0"]
            top = r["top"]

            sec = _section_of(top)
            grd = _grid_of(x0)
            if sec >= 0 and grd >= 0:
                col = _col_of(x0, grd)
                if col >= 0:
                    yr_off, month = _MONTH_GRID[sec][grd]
                    year = start_year + yr_off

                    # Compute row_idx
                    first_row_top = _SECTION_TOPS[sec] + 19.94 + 18.38
                    row_idx = max(0, int((top - first_row_top) / 21.6))

                    # Calculate date
                    first_day_date = date(year, month, 1)
                    first_day_weekday = first_day_date.isoweekday() % 7 # Sunday = 0

                    day = row_idx * 7 + col - first_day_weekday + 1

                    try:
                        d = date(year, month, day)
                        ctype = _COLLECTION_COLORS[c]
                        collections[ctype].add(d)
                    except ValueError:
                        # Day out of range
                        pass

    # Extract Christmas tree dates from raw text
    xmas_dates = _extract_christmas_tree_dates(raw_text, end_year, sector)
    xmas_dates = [d for d in xmas_dates if d.month == 1]
    if not xmas_dates:
        xmas_dates = (
            [date(end_year, 1, 7), date(end_year, 1, 14)]
            if sector == "A"
            else [date(end_year, 1, 6), date(end_year, 1, 13)]
        )

    # Convert sets to sorted lists
    schedule = {
        "start_year": start_year,
        "end_year": end_year,
        "christmas_trees": sorted(xmas_dates),
    }
    for ctype in collections:
        schedule[ctype] = sorted(list(collections[ctype]))

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
    Generates discrete VEVENT entries for each specific collection day.
    """
    try:
        from icalendar import Calendar, Event  # type: ignore[import]
    except ImportError:
        print("[error] icalendar is not installed. Run: pip install icalendar")
        sys.exit(1)

    sy = schedule["start_year"]
    ey = schedule["end_year"]

    def _dt(d: date) -> datetime:
        return datetime(d.year, d.month, d.day, 7, 0, 0)

    cal = Calendar()
    cal.add("prodid", f"-//Pointe-Claire//Waste Collection Sector {sector}//EN")
    cal.add("version", "2.0")
    cal.add("x-wr-calname", f"Pointe-Claire Sector {sector} Collection {sy}-{ey}")

    # Event definitions: (key, summary, description)
    event_defs = [
        ("organic", "Organic Waste", "Matières organiques collection."),
        ("recyclables", "Recyclables", "Matières recyclables collection."),
        ("household", "Household Waste", "Déchets domestiques collection."),
        ("bulky", "Bulky Items", "Encombrants collection."),
        ("leaf", "Leaf Collection", "Collecte saisonnière de feuilles."),
        ("mattress", "Mattress/Box-Spring Collection", "Collecte de matelas et sommiers."),
        ("ecocentre", "Ecocentre Collection", "Collecte éco-centre / Ecocentre collection."),
    ]

    for key, summary, desc in event_defs:
        dates_list = schedule.get(key, [])
        for d in dates_list:
            ev = Event()
            ev.add("summary", f"{summary} - Sector {sector}")
            ev.add("dtstart", _dt(d))
            ev.add("duration", timedelta(hours=1))
            ev.add("description", desc)
            cal.add_component(ev)

    # Christmas Trees
    for d in schedule.get("christmas_trees", []):
        ev = Event()
        ev.add("summary", f"Christmas Tree Collection - Sector {sector}")
        ev.add("dtstart", _dt(d))
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
    print(f"  Organic:      {len(schedule['organic'])} events")
    print(f"  Recyclables:  {len(schedule['recyclables'])} events")
    print(f"  Household:    {len(schedule['household'])} events")
    print(f"  Bulky:        {len(schedule['bulky'])} events")
    print(f"  Leaf:         {len(schedule['leaf'])} events")
    print(f"  Mattress:     {len(schedule['mattress'])} events")
    print(f"  Ecocentre:    {len(schedule['ecocentre'])} events")
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
