import pytest

from app.translation_schemas import (
    TranslationCreate,
    TranslationListParams,
    normalize_language,
)

LANGUAGE_CASES = [
    ("PT", "pt"),
    ("pt", "pt"),
    ("es-mx", "es-MX"),
    ("ES-mx", "es-MX"),
    ("es-MX", "es-MX"),
]


@pytest.mark.parametrize("language,expected", LANGUAGE_CASES)
def test_normalize_language(language: str, expected: str) -> None:
    assert normalize_language(language) == expected


@pytest.mark.parametrize("language,expected", LANGUAGE_CASES)
def test_translation_create_normalizes_language(language: str, expected: str) -> None:
    translation = TranslationCreate(oracle_id="oracle-1", lang=language, name="Nombre")

    assert translation.lang == expected


@pytest.mark.parametrize("language,expected", LANGUAGE_CASES)
def test_translation_list_normalizes_language(language: str, expected: str) -> None:
    params = TranslationListParams(lang=language)

    assert params.lang == expected


def test_translation_list_allows_omitted_language() -> None:
    assert TranslationListParams().lang is None
    assert TranslationListParams(lang=None).lang is None
