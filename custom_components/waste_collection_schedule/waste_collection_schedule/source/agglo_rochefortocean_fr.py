"""Waste collection for Rochefort (Rochefort Océan), from the annual colour PDF."""

from typing import ClassVar, final

from waste_collection_schedule import parsers, retrievers
from waste_collection_schedule import waste_types as wt
from waste_collection_schedule.base_source import BaseSource
from waste_collection_schedule.config_params import dropdown
from waste_collection_schedule.service.FrenchColourCalendar import rochefort_ocean_rows
from waste_collection_schedule.transformers import ICSTransformer

_INDEX = "https://www.agglo-rochefortocean.fr/collecte-et-tri"
_PDF = (
    r"/CALENDRIER(?:%20| )+(?:COLLECTE(?:%20| )+)?"
    r"ROCHEFORT(?:%20| )+(20\d\d)\.pdf$"
)


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
    preprocess = staticmethod(rochefort_ocean_rows)
    transform = ICSTransformer(
        type_value_map={"general": wt.GENERAL_WASTE, "recycling": wt.RECYCLABLES}
    )
