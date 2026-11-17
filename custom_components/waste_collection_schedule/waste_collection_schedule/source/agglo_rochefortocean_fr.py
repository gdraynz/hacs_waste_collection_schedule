"""Waste collection for Rochefort (Rochefort Océan), from the annual colour PDF."""

import calendar
import colorsys
import re
from datetime import date, timedelta
from typing import ClassVar, TypedDict, final

from waste_collection_schedule import parsers, retrievers
from waste_collection_schedule import waste_types as wt
from waste_collection_schedule.base_source import BaseSource
from waste_collection_schedule.config_params import dropdown
from waste_collection_schedule.exceptions import SourceArgumentNotFoundWithSuggestions
from waste_collection_schedule.parsers import PdfTextFragment, PdfWordLayout
from waste_collection_schedule.response_shape import expect
from waste_collection_schedule.service.FrenchColourCalendar import (
    MONTHS,
    background_colour,
    normalise,
)
from waste_collection_schedule.transformers import ICSTransformer

_INDEX = "https://www.agglo-rochefortocean.fr/collecte-et-tri"
_PDF = (
    r"/CALENDRIER(?:%20| )+(?:COLLECTE(?:%20| )+)?"
    r"ROCHEFORT(?:%20| )+(20\d\d)\.pdf$"
)


def _require(condition: bool, detail: str) -> None:
    """A failed check means the printed layout changed, not the user's answer."""
    expect(condition, source_name="Rochefort Océan", detail=detail, raw="")


# The provider prints a 3-column by 4-block mini-month calendar per year,
# with every week filled green or blue. The colour alternates continuously
# across years rather than by ISO parity: 2026 has 53 ISO weeks, so its odd
# weeks are green and 2027's even weeks are green. The printed document is
# therefore the authority for the week colour; a `week % 2` rule would be
# wrong after the flip. A sector's general-waste weekday applies on its
# colour's weeks outside June-September, when it applies every week, while
# packaging is collected on a fixed weekday on the sector's colour's weeks
# all year round.

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
    token = normalise(text)
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
    titles = [p for p in layout.lines if normalise(p.text) in MONTHS]
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
            (MONTHS[normalise(title.text)], title.y, page, header_y, columns)
        )

    for page in {header[2] for header in headers}:
        on_page = [header for header in headers if header[2] == page]
        if len({header[0] for header in on_page}) == 12:
            return sorted(on_page, key=lambda header: -header[1])

    _require(False, "expected one complete twelve-month calendar grid")
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
                _require(d not in cells, f"duplicate calendar date {d}")
                cells[d] = word
                found += 1
        # One printed date can be missing (2026-08-31 is not drawn in the
        # August grid); a changed layout loses many more than that.
        _require(found >= 27, f"month {month}: incomplete day grid")
    return cells


def _printed_year(layout: PdfWordLayout) -> int:
    """The one year printed in a title such as "CALENDRIER DE COLLECTE 2026"."""
    years = {
        int(year)
        for line in layout.lines
        for year in re.findall(r"\b20\d\d\b", line.text)
    }
    _require(len(years) == 1, "calendar has no unambiguous printed year")
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


def collection_rows(documents, source=None):
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
            colour = _week_colour(background_colour(layout, fragment))
            if colour is not None:
                weeks.setdefault(d - timedelta(days=d.weekday()), set()).add(colour)
        _require(len(weeks) >= 52, "calendar week colours are incomplete")

        for monday, colours in weeks.items():
            _require(len(colours) == 1, "week has mixed colours")
            colour = next(iter(colours))
            # A week at the year boundary is printed in both documents; their
            # colours must agree rather than one silently winning.
            if monday in seen_weeks:
                _require(
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


@final
class Source(BaseSource):
    TITLE = "Rochefort Océan (Rochefort)"
    DESCRIPTION = "Waste collection dates for Rochefort (Rochefort Océan), France."
    URL = _INDEX
    COUNTRY = "fr"
    RAISE_ON_EMPTY = True
    SOURCE_CODEOWNERS: ClassVar[list[str]] = ["@gdraynz"]

    PARAMS = (dropdown("sector", [str(i) for i in range(1, 10)], label="Secteur"),)
    WASTE_TYPES: ClassVar[list] = [wt.GENERAL_WASTE, wt.RECYCLABLES]

    TEST_CASES: ClassVar[dict] = {
        "Secteur 1": {"sector": "1"},
        "Secteur 9": {"sector": "9"},
    }

    HOWTO: ClassVar[dict[str, str]] = {
        "en": (
            "Your sector is the one shown on the map printed on the back of the "
            "yearly “Calendrier de collecte Rochefort” PDF, which also lists the "
            "collection days. Collections are maintained on public holidays."
        ),
        "fr": (
            "Votre secteur est celui de la carte imprimée au verso du calendrier "
            "de collecte annuel de Rochefort, qui indique aussi les jours de "
            "collecte. Les collectes sont maintenues les jours fériés."
        ),
    }

    retrieve = retrievers.FanOutRetriever(
        prepare=retrievers.Lookup(
            _INDEX,
            pick=retrievers.yearly_links(_PDF),
            raise_for_status=True,
        ),
        targets=lambda source, urls: urls,
        fetch=retrievers.Request(lambda url, urls, **_: url, timeout=60, cache=True),
    )
    parse = parsers.EachResponse(
        parsers.PdfWordLayoutParser(min_words=300, min_vectors=100)
    )
    preprocess = staticmethod(collection_rows)
    transform = ICSTransformer(
        type_value_map={"general": wt.GENERAL_WASTE, "recycling": wt.RECYCLABLES}
    )
