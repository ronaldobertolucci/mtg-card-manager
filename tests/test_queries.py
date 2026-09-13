import pytest
from pydantic import ValidationError

from app.repository import _attribute_filter, _oracle_card_filter, _text_filter
from app.schemas import SearchParams


def test_text_filter_escapes_regex_metacharacters() -> None:
    query = _text_filter(SearchParams(name="Black (Lotus)"))
    assert query == {"name": {"$regex": "Black\\ \\(Lotus\\)", "$options": "i"}}


def test_cmc_range_query() -> None:
    query = _attribute_filter(SearchParams(colors="U,R", cmc_gte=2, cmc_lte=4))
    assert query == {"colors": ["U", "R"], "cmc": {"$gte": 2.0, "$lte": 4.0}}


def test_localized_card_query_uses_scryfall_oracle_id_not_printing_id() -> None:
    query = _oracle_card_filter(
        SearchParams(lang="pt-BR", colors="U"),
        ["oracle-1", "oracle-2"],
    )

    assert query == {
        "colors": ["U"],
        "oracle_id": {"$in": ["oracle-1", "oracle-2"]},
    }


@pytest.mark.parametrize(
    "values",
    [
        {},
        {"name": "x", "cmc": 2, "cmc_gte": 1},
        {"name": "x", "cmc_gte": 5, "cmc_lte": 2},
        {"colors": "R,R"},
        {"colors": "X"},
    ],
)
def test_conflicting_or_invalid_parameters(values: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        SearchParams(**values)
