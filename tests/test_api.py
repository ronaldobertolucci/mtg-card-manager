from collections.abc import Mapping, Sequence
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.api import get_service
from app.main import app
from app.schemas import SearchParams
from app.service import CardSearchService


@pytest.fixture
def card_client():
    repository = AsyncMock()
    repository.get_by_oracle_id.return_value = {
        "_id": "oracle-1",
        "id": "printing-1",
        "oracle_id": "oracle-1",
        "name": "Lightning Bolt",
        "oracle_text": "Deal 3 damage.",
        "flavor_text": "English flavor.",
        "colors": ["R"],
    }
    repository.get_translations.return_value = {
        "oracle-1": {"name": "Raio", "oracle_text": "Causa 3 pontos de dano."}
    }
    original_lifespan = app.router.lifespan_context
    app.router.lifespan_context = no_database_lifespan
    app.dependency_overrides[get_service] = lambda: CardSearchService(repository)
    try:
        with TestClient(app) as client:
            yield client, repository
    finally:
        app.dependency_overrides.clear()
        app.router.lifespan_context = original_lifespan


def test_get_card_by_oracle_id_in_english(card_client) -> None:
    client, repository = card_client
    response = client.get("/cards/oracle-1", params={"lang": "en"})
    assert response.status_code == 200
    card = response.json()
    assert card["oracle_id"] == "oracle-1"
    assert card["id"] == "printing-1"
    assert card["name"] == "Lightning Bolt"
    assert card["lang"] == "en"
    assert "_id" not in card
    repository.get_by_oracle_id.assert_awaited_once_with("oracle-1")
    repository.get_translations.assert_not_awaited()


def test_get_card_defaults_to_portuguese(card_client) -> None:
    client, repository = card_client
    response = client.get("/cards/oracle-1")
    assert response.status_code == 200
    card = response.json()
    assert card["name"] == "Raio"
    assert card["lang"] == "pt-BR"
    assert card["oracle_text"] == "Causa 3 pontos de dano."
    assert card["flavor_text"] is None
    assert card["colors"] == ["R"]
    repository.get_translations.assert_awaited_once_with(["oracle-1"], "pt-BR")


def test_get_missing_card_returns_404(card_client) -> None:
    client, repository = card_client
    repository.get_by_oracle_id.return_value = None
    assert client.get("/cards/missing").status_code == 404
    repository.get_translations.assert_not_awaited()


def test_get_card_without_translation_returns_404(card_client) -> None:
    client, repository = card_client
    repository.get_translations.return_value = {}
    assert client.get("/cards/oracle-1").status_code == 404


@pytest.mark.parametrize(
    "url", ["/cards/invalid!", "/cards/" + "a" * 101, "/cards/oracle-1?lang=invalid"]
)
def test_get_card_validates_parameters(card_client, url) -> None:
    client, repository = card_client
    assert client.get(url).status_code == 422
    repository.get_by_oracle_id.assert_not_awaited()


class EmptyRepository:
    async def search_translation_matches(self, params: SearchParams) -> dict[str, list[int] | None]:
        return {}

    async def search_oracle_cards(
        self, params: SearchParams, oracle_ids: Mapping[str, list[int] | None] | None = None
    ) -> list[dict[str, Any]]:
        return []

    async def get_translations(
        self, oracle_ids: Sequence[str], lang: str
    ) -> Mapping[str, dict[str, Any]]:
        return {}


@asynccontextmanager
async def no_database_lifespan(application: Any):
    yield


def test_invalid_query_returns_400() -> None:
    original_lifespan = app.router.lifespan_context
    app.router.lifespan_context = no_database_lifespan
    app.dependency_overrides[get_service] = lambda: CardSearchService(EmptyRepository())
    try:
        with TestClient(app) as client:
            response = client.get("/cards/search")
    finally:
        app.dependency_overrides.clear()
        app.router.lifespan_context = original_lifespan
    assert response.status_code == 400


def test_no_matches_returns_404() -> None:
    original_lifespan = app.router.lifespan_context
    app.router.lifespan_context = no_database_lifespan
    app.dependency_overrides[get_service] = lambda: CardSearchService(EmptyRepository())
    try:
        with TestClient(app) as client:
            response = client.get("/cards/search", params={"lang": "en", "name": "missing"})
    finally:
        app.dependency_overrides.clear()
        app.router.lifespan_context = original_lifespan
    assert response.status_code == 404
