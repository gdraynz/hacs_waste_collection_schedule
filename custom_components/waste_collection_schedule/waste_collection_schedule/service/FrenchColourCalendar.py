"""Dated French PDF calendars with colour-coded collection areas.

Read the printed month grids and area legends, including holiday replacements.
Never project dates beyond the published documents.
"""

from __future__ import annotations

import calendar
import colorsys
import re
import unicodedata
from collections import Counter
from datetime import date, timedelta
from typing import TypedDict

from waste_collection_schedule.exceptions import SourceArgumentNotFoundWithSuggestions
from waste_collection_schedule.parsers import PdfTextFragment, PdfVector, PdfWordLayout
from waste_collection_schedule.response_shape import expect

_MONTHS = dict(
    zip(
        "janvier fevrier mars avril mai juin juillet aout septembre octobre novembre decembre".split(),
        range(1, 13),
        strict=True,
    )
)
_DAYS = "lundi mardi mercredi jeudi vendredi samedi dimanche".split()
_SHORT_DAYS = "lu ma me je ve sa di".split()
GRAND_LIEU_COMMUNES = [
    "Le Bignon",
    "Geneston",
    "La Limouzinière",
    "Montbert",
    "Saint Colomban",
    "Saint Lumine de Coutais",
]


def _normalise(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.casefold())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join(re.findall(r"[a-z0-9]+", text))


def _check(condition: bool, detail: str) -> None:
    expect(condition, source_name="French colour calendar", detail=detail, raw="")


def _close(a: tuple[float, ...] | None, b: tuple[float, ...] | None) -> bool:
    return bool(
        a
        and b
        and len(a) == len(b)
        and max(abs(x - y) for x, y in zip(a, b, strict=True)) < 0.025
    )


def _contains(v: PdfVector, p: PdfTextFragment) -> bool:
    if not (v.page == p.page and v.x0 <= p.x <= v.x1 and v.y0 <= p.y <= v.y1):
        return False
    points = getattr(v, "points", ())
    if not points:
        return True
    inside = False
    for (x1, y1), (x2, y2) in zip(points, (*points[1:], points[0]), strict=True):
        if (y1 > p.y) != (y2 > p.y) and p.x < (x2 - x1) * (p.y - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


def _background(layout: PdfWordLayout, p: PdfTextFragment, colours=None):
    candidates = [
        v
        for v in layout.vectors
        if v.fill_color
        and _contains(v, p)
        and (colours is None or any(_close(v.fill_color, c) for c in colours))
    ]
    candidates.sort(key=lambda v: (v.x1 - v.x0) * (v.y1 - v.y0))
    return candidates[0].fill_color if candidates else None


def _headers(layout: PdfWordLayout):
    headers = [
        p for p in layout.words if p.text.isupper() and _normalise(p.text) in _MONTHS
    ]
    pages = [page for page, n in Counter(p.page for p in headers).items() if n == 12]
    _check(len(pages) == 1, "expected one complete twelve-month calendar grid")
    return [p for p in headers if p.page == pages[0]]


def _year(layout: PdfWordLayout) -> int:
    years = {int(p.text) for p in layout.lines if re.fullmatch(r"20\d\d", p.text)}
    _check(len(years) == 1, "calendar has no unambiguous printed year")
    return years.pop()


def _grid_cells(layout: PdfWordLayout, headers: list[PdfTextFragment], year: int):
    """Read mini-months and verify every date against its weekday column."""
    cells = {}
    for header in headers:
        month = _MONTHS[_normalise(header.text)]
        neighbours = [
            abs(p.x - header.x)
            for p in headers
            if abs(p.y - header.y) < 5 and p != header
        ]
        width = min(neighbours)
        columns = {
            _SHORT_DAYS.index(_normalise(p.text)): p.x
            for p in layout.words
            if p.page == header.page
            and 0 < header.y - p.y < 20
            and abs(p.x - header.x) < width / 2
            and _normalise(p.text) in _SHORT_DAYS
        }
        _check(len(columns) == 7, f"month {month}: missing weekday headers")
        next_y = max(
            (p.y for p in headers if p.y < header.y - 5), default=header.y - 150
        )
        for p in layout.words:
            if p.page != header.page or not re.fullmatch(r"\d{1,2}", p.text):
                continue
            if not (next_y + 10 < p.y < header.y - 20):
                continue
            day = int(p.text)
            if not 1 <= day <= calendar.monthrange(year, month)[1]:
                continue
            d = date(year, month, day)
            if abs(p.x - columns[d.weekday()]) < 4:
                _check(d not in cells, f"duplicate calendar date {d}")
                cells[d] = p
        expected = {
            date(year, month, d)
            for d in range(1, calendar.monthrange(year, month)[1] + 1)
        }
        _check(
            expected <= cells.keys(),
            f"month {month}: missing dates or weekday columns changed",
        )
    return cells


def syvalorm_rows(documents, source=None):
    """Resolve an area's printed map colour separately for every calendar year."""
    wanted = _normalise(source.params["municipality"])
    rows = []
    for layout in documents:
        headers = _headers(layout)
        cells = _grid_cells(layout, headers, _year(layout))
        colours = {
            c
            for p in cells.values()
            if (c := _background(layout, p)) and len(c) == 4 and max(c[:3]) > 0.1
        }
        _check(len(colours) >= 8, "calendar collection colours changed")
        # The map names every commune. A second legend on the calendar page
        # lists changed rounds and takes precedence over the map.
        matches = [p for p in layout.lines if _normalise(p.text) == wanted]
        selected = []
        for p in matches:
            swatches = [
                v
                for v in layout.vectors
                if v.page == p.page
                and p.page == headers[0].page
                and v.y0 <= p.y <= v.y1
                and 0 < p.x - v.x1 < 50
                and 5 < v.x1 - v.x0 < 25
                and any(_close(v.fill_color, c) for c in colours)
            ]
            if swatches:
                selected.append(min(swatches, key=lambda v: p.x - v.x1).fill_color)
            elif colour := _background(layout, p, colours):
                selected.append(colour)
        if not selected:
            raise SourceArgumentNotFoundWithSuggestions(
                "municipality",
                source.params["municipality"],
                sorted(
                    {p.text for p in layout.lines if _background(layout, p, colours)}
                ),
            )
        colour = selected[-1]
        days = sorted(
            d for d, p in cells.items() if _close(_background(layout, p), colour)
        )
        _check(
            24 <= len(days) <= 28,
            "expected a fortnightly collection calendar for the selected area",
        )
        rows.extend((d, label) for d in days for label in ("general", "recycling"))
    return sorted(set(rows))


def grand_lieu_rows(documents, source=None):
    """Read the six-commune week colours and the PDF's holiday-to-Saturday rule."""
    wanted = _normalise(source.params["municipality"])
    valid = {_normalise(name): name for name in GRAND_LIEU_COMMUNES}
    if wanted not in valid:
        raise SourceArgumentNotFoundWithSuggestions(
            "municipality", source.params["municipality"], GRAND_LIEU_COMMUNES
        )
    rows = []
    for layout in documents:
        headers = _headers(layout)
        page = headers[0].page
        year = _year(layout)
        legend_name = "saint lumine" if wanted == "saint lumine de coutais" else wanted
        names = [
            p
            for p in layout.lines
            if p.page == page and _normalise(p.text) == legend_name
        ]
        _check(len(names) == 1, "municipality missing from calendar legend")
        name = names[0]
        weekdays = [
            p
            for p in layout.words
            if p.page == page and _normalise(p.text) in _DAYS and abs(p.y - name.y) < 8
        ]
        _check(len(weekdays) == 1, "municipality collection weekday missing")
        weekday = weekdays[0]
        colour = _background(layout, weekday)
        _check(colour is not None, "municipality week colour missing")
        # Month headers and day numbers are positioned in twelve vertical
        # columns. Build bands at their midpoints, including split text runs.
        headers = sorted(headers, key=lambda p: p.x)
        cells = {}
        for i, header in enumerate(headers):
            left = (headers[i - 1].x + header.x) / 2 if i else header.x - 30
            right = (header.x + headers[i + 1].x) / 2 if i < 11 else header.x + 30
            month = _MONTHS[_normalise(header.text)]
            lines = [
                p
                for p in layout.lines
                if p.page == page and left <= p.x < right and p.y < header.y
            ]
            for p in lines:
                m = re.fullmatch(r"([LMMJVSD])\s*(\d{1,2})", p.text)
                if not m:
                    continue
                d = date(year, month, int(m[2]))
                _check(m[1] == "LMMJVSD"[d.weekday()], f"weekday mismatch for {d}")
                _check(d not in cells, f"duplicate date {d}")
                cells[d] = (p, [x.text for x in lines if abs(x.y - p.y) < 3])
            _check(
                sum(d.month == month for d in cells)
                == calendar.monthrange(year, month)[1],
                f"month {month}: incomplete day grid",
            )
        matching = [
            d for d, (p, _) in cells.items() if _close(_background(layout, p), colour)
        ]
        parities = {d.isocalendar().week % 2 for d in matching}
        _check(
            len(parities) == 1 and len(matching) > 100,
            "calendar week colour is inconsistent",
        )
        parity = parities.pop()
        text = _normalise(" ".join(p.text for p in layout.lines))
        _check(
            "samedi suivant" in text and "jours feries" in text,
            "holiday replacement rule changed",
        )
        for d, (_, labels) in cells.items():
            if (
                d.weekday() != _DAYS.index(_normalise(weekday.text))
                or d.isocalendar().week % 2 != parity
            ):
                continue
            if any(_normalise(label) == "ferie" for label in labels):
                d += timedelta(days=(5 - d.weekday()) % 7)
            rows.extend((d, label) for label in ("general", "recycling"))
    return sorted(set(rows))


# --- Rochefort Océan -------------------------------------------------------
#
# Rochefort prints a 3-column by 4-block mini-month calendar per year, with
# every week filled green or blue. The colour alternates continuously across
# years rather than by ISO parity: 2026 has 53 ISO weeks, so its odd weeks are
# green and 2027's even weeks are green. The printed document is therefore the
# authority for the week colour; a `week % 2` rule would be wrong after the
# flip. A sector's general-waste weekday applies on its colour's weeks outside
# June-September, when it applies every week, while packaging is collected on
# a fixed weekday on the sector's colour's weeks all year round.

_DAY_INITIALS = "lmmjvsd"
_WEEKDAY_TOKENS = (
    frozenset({"l", "lu", "lun", "lundi"}),
    frozenset({"ma", "mar", "mardi"}),
    frozenset({"me", "mer", "mercredi"}),
    frozenset({"j", "je", "jeu", "jeudi"}),
    frozenset({"v", "ve", "ven", "vendredi"}),
    frozenset({"s", "sa", "sam", "samedi"}),
    frozenset({"d", "di", "dim", "dimanche"}),
)


class _SectorRule(TypedDict):
    om: tuple[int, ...]
    om_weeks: str
    pack: int
    pack_weeks: str


ROCHEFORT_SECTORS: dict[str, _SectorRule] = {
    "1": {"om": (0,), "om_weeks": "green", "pack": 3, "pack_weeks": "green"},
    "2": {"om": (0,), "om_weeks": "blue", "pack": 3, "pack_weeks": "green"},
    "3": {"om": (1,), "om_weeks": "green", "pack": 3, "pack_weeks": "green"},
    "4": {"om": (1,), "om_weeks": "blue", "pack": 3, "pack_weeks": "green"},
    "5": {"om": (2,), "om_weeks": "green", "pack": 3, "pack_weeks": "blue"},
    "6": {"om": (2,), "om_weeks": "blue", "pack": 3, "pack_weeks": "blue"},
    "7": {"om": (4,), "om_weeks": "green", "pack": 3, "pack_weeks": "blue"},
    "8": {"om": (4,), "om_weeks": "blue", "pack": 3, "pack_weeks": "blue"},
    "9": {"om": (1, 5), "om_weeks": "both", "pack": 2, "pack_weeks": "both"},
}


def _is_weekday_token(text: str, weekday: int) -> bool:
    token = _normalise(text)
    return token in _WEEKDAY_TOKENS[weekday] or token == _DAY_INITIALS[weekday]


def _append_weekday_chunk(
    chunks: list[tuple[int, float, tuple[float, ...]]],
    page: int,
    run: list[PdfTextFragment],
) -> None:
    if len(run) == 7 and all(
        _is_weekday_token(word.text, weekday) for weekday, word in enumerate(run)
    ):
        chunks.append((page, run[0].y, tuple(word.x for word in run)))


def _weekday_chunks(
    layout: PdfWordLayout,
) -> list[tuple[int, float, tuple[float, ...]]]:
    """Every mini-month's weekday header, as its seven column x positions.

    The header is a run of seven weekday words on one line, separated from the
    next mini-month's run by a wider gap. Both printed forms are accepted: the
    three-letter names, and the single initials (including the ambiguous "M"
    for both mardi and mercredi).
    """
    chunks: list[tuple[int, float, tuple[float, ...]]] = []
    for page in sorted({p.page for p in layout.words}):
        rows: list[list[PdfTextFragment]] = []
        for word in sorted(
            (p for p in layout.words if p.page == page),
            key=lambda p: (-p.y, p.x),
        ):
            # Group against the row's first word, not its last: a pdfminer
            # text column can otherwise chain one row into the next.
            if rows and abs(rows[-1][0].y - word.y) < 3:
                rows[-1].append(word)
            else:
                rows.append([word])
        for row in rows:
            row.sort(key=lambda p: p.x)
            run: list[PdfTextFragment] = []
            for word in row:
                if run and word.x - run[-1].x > 30:
                    _append_weekday_chunk(chunks, page, run)
                    run = []
                run.append(word)
            _append_weekday_chunk(chunks, page, run)
    return chunks


def _block_headers(
    layout: PdfWordLayout,
) -> list[tuple[int, float, int, float, tuple[float, ...]]]:
    """Month title, title y, page, header y and columns for every mini-month.

    Only the page carrying all twelve month titles is used, so month names in
    the calendar's legend or instructions cannot be mistaken for grid headers.
    """
    titles = [p for p in layout.lines if _normalise(p.text) in _MONTHS]
    headers: list[tuple[int, float, int, float, tuple[float, ...]]] = []
    for page, header_y, columns in _weekday_chunks(layout):
        above = [
            p
            for p in titles
            if p.page == page
            and 0 < p.y - header_y < 20
            and columns[0] - 10 <= p.x <= columns[-1] + 10
        ]
        if not above:
            continue
        title = min(above, key=lambda p: p.y - header_y)
        headers.append(
            (_MONTHS[_normalise(title.text)], title.y, page, header_y, columns)
        )

    for page in {header[2] for header in headers}:
        on_page = [header for header in headers if header[2] == page]
        if len({header[0] for header in on_page}) == 12:
            return sorted(on_page, key=lambda header: -header[1])

    _check(False, "expected one complete twelve-month calendar grid")
    return []


def _block_grid_cells(
    layout: PdfWordLayout,
    headers: list[tuple[int, float, int, float, tuple[float, ...]]],
    year: int,
) -> dict[date, PdfTextFragment]:
    """Read each mini-month's day numbers, checked against its weekday column."""
    cells: dict[date, PdfTextFragment] = {}
    page = headers[0][2]
    for month, title_y, _page, header_y, columns in headers:
        next_y = max(
            (header[1] for header in headers if header[1] < title_y - 5),
            default=title_y - 150,
        )
        found = 0
        for word in layout.words:
            if word.page != page or not re.fullmatch(r"\d{1,2}", word.text):
                continue
            if not (next_y + 10 < word.y < header_y - 5):
                continue
            day = int(word.text)
            if not 1 <= day <= calendar.monthrange(year, month)[1]:
                continue
            d = date(year, month, day)
            if abs(word.x - columns[d.weekday()]) < 4:
                _check(d not in cells, f"duplicate calendar date {d}")
                cells[d] = word
                found += 1
        # One printed date can be missing (2026-08-31 is not drawn in the
        # August grid); a changed layout loses many more than that.
        _check(found >= 27, f"month {month}: incomplete day grid")
    return cells


def _printed_year(layout: PdfWordLayout) -> int:
    """The one year printed in a title such as "CALENDRIER DE COLLECTE 2026"."""
    years = {
        int(year)
        for line in layout.lines
        for year in re.findall(r"\b20\d\d\b", line.text)
    }
    _check(len(years) == 1, "calendar has no unambiguous printed year")
    return years.pop()


def _to_rgb(color: tuple[float, ...] | None) -> tuple[float, ...] | None:
    """Normalise a PDF colour to RGB, converting a four-component CMYK fill."""
    if color is None:
        return None
    if len(color) == 1:
        return (color[0],) * 3
    if len(color) == 3:
        return color
    if len(color) == 4:
        c, m, y, k = color
        return ((1 - c) * (1 - k), (1 - m) * (1 - k), (1 - y) * (1 - k))
    return None


def _week_colour(color: tuple[float, ...] | None) -> str | None:
    rgb = _to_rgb(color)
    if rgb is None:
        return None
    hue, saturation, _value = colorsys.rgb_to_hsv(*rgb)
    if saturation > 0.25 and 0.15 <= hue <= 0.45:
        return "green"
    if saturation > 0.2 and 0.50 <= hue <= 0.75:
        return "blue"
    return None


def rochefort_ocean_rows(documents, source=None):
    """Turn the printed week colours into a sector's collection dates."""
    sector = str(source.params["sector"])
    rule = ROCHEFORT_SECTORS.get(sector)
    if rule is None:
        raise SourceArgumentNotFoundWithSuggestions(
            "sector", sector, sorted(ROCHEFORT_SECTORS)
        )

    rows = []
    seen_weeks: dict[date, str] = {}
    for layout in documents:
        headers = _block_headers(layout)
        year = _printed_year(layout)
        cells = _block_grid_cells(layout, headers, year)

        # Group painted days into weeks. Iterating weeks rather than cells is
        # what still emits a day the grid happens not to draw (2026-08-31).
        weeks: dict[date, set[str]] = {}
        for d, fragment in cells.items():
            colour = _week_colour(_background(layout, fragment))
            if colour is not None:
                weeks.setdefault(d - timedelta(days=d.weekday()), set()).add(colour)
        _check(len(weeks) >= 52, "calendar week colours are incomplete")

        for monday, colours in weeks.items():
            _check(len(colours) == 1, "week has mixed colours")
            colour = next(iter(colours))
            # A week at the year boundary is printed in both documents; their
            # colours must agree rather than one silently winning.
            if monday in seen_weeks:
                _check(
                    seen_weeks[monday] == colour,
                    "year-boundary week colours disagree between the calendars",
                )
            seen_weeks[monday] = colour

        for monday, colours in weeks.items():
            colour = next(iter(colours))
            for weekday in range(7):
                d = monday + timedelta(days=weekday)
                if d.year != year:
                    continue
                # The provider applies the summer rule per collection date's
                # month; a week straddling May/June or September/October is
                # ambiguous, affecting at most one or two collections a year.
                if weekday in rule["om"] and (
                    d.month in (6, 7, 8, 9) or rule["om_weeks"] in (colour, "both")
                ):
                    rows.append((d, "general"))
                if weekday == rule["pack"] and rule["pack_weeks"] in (
                    colour,
                    "both",
                ):
                    rows.append((d, "recycling"))
    return sorted(set(rows))
