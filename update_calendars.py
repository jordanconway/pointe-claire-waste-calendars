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
  The single-page PDF contains either a 3-column × 4-row grid of monthly
  calendars covering April (start_year) through March (end_year), or a
  2-column × 4-row mid-year layout covering August through March. Each day cell
  may be filled with a colour indicating the collection type scheduled that day:

  CMYK colour               Collection type
  ─────────────────────────────────────────
  (0.40, 0.00, 0.80, 0.00)  Organic waste     (Green)
  (1.00, 0.76, 0.00, 0.00)  Recyclables       (Blue)
  (0.00, 0.00, 0.00, 0.75)  Household waste   (Grey; Tue/Wed depending on sector & season)
  (0.25, 0.85, 0.85, 0.25)  Leaf collection   (Brown)
  (0.45, 0.70, 0.00, 0.00)  Mattress/box-spring (Purple/Brown)
  (0.82, 0.19, 0.44, 0.23)  Christmas trees   (Burgundy/Pink)
  (0.00, 0.52, 0.75, 0.00)  Bulky items       (Orange)
  (0.00, 0.20, 0.85, 0.00)  Ecocentre / HHW   (Yellow-Orange)
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
    r'https?://[^"\'>\s]*?collectes-\d{4}-.*?([AB])\.pdf',
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
# 3-column layout (12-month calendar, Apr–Mar)
_GRID_X_STARTS = [436.0, 859.3, 1282.0]   # default / 3-col
_MONTH_GRID = [
    [(0, 4), (0, 8), (0, 12)],
    [(0, 5), (0, 9), (1, 1)],
    [(0, 6), (0, 10), (1, 2)],
    [(0, 7), (0, 11), (1, 3)],
]

# 2-column layout (8-month mid-year update, Aug–Mar)
_GRID_X_STARTS_2COL = [436.0, 859.3]
_MONTH_GRID_2COL = [
    [(0, 8), (0, 12)],
    [(0, 9), (1, 1)],
    [(0, 10), (1, 2)],
    [(0, 11), (1, 3)],
]

_COL_WIDTH = 55.3                           # width of one day column
_SECTION_TOPS = [18, 171, 325, 479, 9999]  # top of each of the 4 month rows (+sentinel)

# col 0 = Sunday … col 6 = Saturday → ISO weekday (Mon=1 … Sun=7)
_COL_TO_ISOWD = [7, 1, 2, 3, 4, 5, 6]

# Collection-colour map (CMYK tuples as returned by pdfplumber)
_COLLECTION_COLORS = {
    (0.40, 0.00, 0.80, 0.00): "organic",         # Green (Apple core)
    (1.00, 0.76, 0.00, 0.00): "recyclables",     # Blue (Blue bin)
    (0.00, 0.00, 0.00, 0.75): "household",       # Grey (Trash can)
    (0.25, 0.85, 0.85, 0.25): "leaf",            # Brown (Leaf collection)
    (0.45, 0.70, 0.00, 0.00): "mattress",        # Purple/Brown (Mattress/box-spring)
    (0.82, 0.19, 0.44, 0.23): "christmas_trees", # Burgundy/Pink (Christmas tree collection)
    (0.00, 0.52, 0.75, 0.00): "bulky",           # Orange (Bulky items)
    (0.00, 0.20, 0.85, 0.00): "ecocentre",       # Yellow-Orange (Ecocentre/HHW)
}


def _section_of(top: float) -> int:
    for i in range(len(_SECTION_TOPS) - 1):
        if _SECTION_TOPS[i] <= top < _SECTION_TOPS[i + 1]:
            return i
    return -1


def _grid_of(x0: float, grid_starts: list[float] | None = None) -> int:
    starts = grid_starts if grid_starts is not None else _GRID_X_STARTS
    for i in range(len(starts) - 1):
        if starts[i] - 5 <= x0 < starts[i + 1] - 5:
            return i
    return len(starts) - 1 if x0 >= starts[-1] - 5 else -1


def _col_of(x0: float, grid_idx: int, grid_starts: list[float] | None = None) -> int:
    starts = grid_starts if grid_starts is not None else _GRID_X_STARTS
    if grid_idx < 0 or grid_idx >= len(starts):
        return -1
    col = round((x0 - starts[grid_idx]) / _COL_WIDTH)
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

    Supports both 3-column (12-month, Apr–Mar) and 2-column (8-month mid-year, Aug–Mar) PDF layouts.
    Returns a schedule dict containing sorted lists of exact dates for each collection type,
    plus start_year, end_year, and start_month.
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
        width = page.width

    is_2col = width < 1400
    grid_starts = _GRID_X_STARTS_2COL if is_2col else _GRID_X_STARTS
    month_grid = _MONTH_GRID_2COL if is_2col else _MONTH_GRID
    start_month = 8 if is_2col else 4

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
            grd = _grid_of(x0, grid_starts)
            if sec >= 0 and grd >= 0:
                col = _col_of(x0, grd, grid_starts)
                if col >= 0:
                    yr_off, month = month_grid[sec][grd]
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

    # Extract Christmas tree dates: use grid dates if found, else parse from text / fallback
    xmas_dates = [d for d in collections.get("christmas_trees", set()) if d.month == 1]
    if not xmas_dates:
        xmas_dates = _extract_christmas_tree_dates(raw_text, end_year, sector)
        xmas_dates = [d for d in xmas_dates if d.month == 1]
        if not xmas_dates:
            xmas_dates = (
                [date(end_year, 1, 7), date(end_year, 1, 14)]
                if sector == "A"
                else [date(end_year, 1, 6), date(end_year, 1, 13)]
            )
    collections["christmas_trees"] = set(xmas_dates)

    # Convert sets to sorted lists
    schedule = {
        "start_year": start_year,
        "end_year": end_year,
        "start_month": start_month,
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
            # Avoid matching 4-digit years like 2027
            m = re.search(
                month_name + r"\s+(?!20\d\d)(\d{1,2})(?:\s+(?:and|et|&)\s+(\d{1,2}))?",
                snippet,
            )
            if not m:
                m = re.search(
                    r"(?<!\d)(\d{1,2})\s+(?:and|et|&)\s+(\d{1,2})\s+" + month_name,
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
                   cached_hashes: dict, new_hashes: dict,
                   force: bool = False) -> bool:
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

    if not force and cached_hashes.get(f"sector_{sector}") == digest:
        print(f"[sector {sector}] PDF unchanged (hash match); skipping update.")
        return False

    print(f"[sector {sector}] PDF changed or new — parsing…")
    schedule = parse_pdf_schedule(pdf_data, sector)

    # If the PDF is a mid-year update (e.g. starts in August), attempt to load pre-update
    # months (April–July) from the full-year 12-month PDF so the calendar remains complete.
    if schedule.get("start_month", 4) > 4:
        base_url = _guess_url(sector)
        if base_url != url:
            print(f"[sector {sector}] Mid-year PDF detected (start_month={schedule['start_month']}). Fetching base 12-month PDF for early months…")
            try:
                base_pdf_data = fetch(base_url)
                base_schedule = parse_pdf_schedule(base_pdf_data, sector)
                cutoff = date(schedule["start_year"], schedule["start_month"], 1)
                for key in set(schedule.keys()) & set(base_schedule.keys()):
                    if isinstance(schedule[key], list):
                        early_dates = [d for d in base_schedule[key] if d < cutoff]
                        schedule[key] = sorted(early_dates + schedule[key])
                print(f"[sector {sector}] Merged early dates (April–July) from base calendar.")
            except Exception as exc:
                print(f"[sector {sector}] Could not fetch base 12-month PDF ({exc}); continuing with mid-year schedule.")

    print(
        f"[sector {sector}] Parsed schedule for "
        f"{schedule['start_year']}–{schedule['end_year']}:"
    )
    for k in sorted(schedule.keys()):
        if isinstance(schedule[k], list):
            print(f"  {k:15}: {len(schedule[k])} events")

    ics_bytes = generate_ics(sector, schedule)
    ics_path.write_bytes(ics_bytes)
    print(f"[sector {sector}] Wrote {ics_path}")
    return True


def main() -> None:
    force = "--force" in sys.argv
    urls = discover_pdf_urls()
    cached = load_hashes()
    new_hashes = dict(cached)

    updated_a = process_sector("A", urls["A"], cached, new_hashes, force=force)
    updated_b = process_sector("B", urls["B"], cached, new_hashes, force=force)

    save_hashes(new_hashes)

    updated = updated_a or updated_b
    print(f"\n[info] {'ICS files updated.' if updated else 'No changes detected.'}")

    gha_output = os.environ.get("GITHUB_OUTPUT")
    if gha_output:
        with open(gha_output, "a") as fh:
            fh.write(f"calendars_updated={'true' if updated else 'false'}\n")


if __name__ == "__main__":
    main()
