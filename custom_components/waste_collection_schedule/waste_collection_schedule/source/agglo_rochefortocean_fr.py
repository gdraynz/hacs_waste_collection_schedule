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
    URL = "https://www.agglo-rochefortocean.fr"
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
            "Find your sector on the map in the “Calendrier de collecte "
            "Rochefort” PDF at https://www.agglo-rochefortocean.fr/collecte-et-tri "
            "and select it here. Collections are maintained on public holidays."
        ),
        "fr": (
            "Repérez votre secteur sur la carte du calendrier de collecte "
            "Rochefort disponible sur https://www.agglo-rochefortocean.fr/collecte-et-tri, "
            "puis sélectionnez-le. Les collectes sont maintenues les jours fériés."
        ),
    }

    retrieve = retrievers.FanOutRetriever(
        prepare=retrievers.Lookup(
            _INDEX,
            pick=retrievers.yearly_links(_PDF),
            raise_for_status=True,
        ),
        targets=lambda source, urls: urls,
        fetch=retrievers.Request(lambda url, urls, **_: url, timeout=60),
    )
    parse = parsers.EachResponse(
        parsers.PdfWordLayoutParser(min_words=300, min_vectors=100)
    )
    preprocess = staticmethod(rochefort_ocean_rows)
    transform = ICSTransformer(
        type_value_map={"general": wt.GENERAL_WASTE, "recycling": wt.RECYCLABLES}
    )
