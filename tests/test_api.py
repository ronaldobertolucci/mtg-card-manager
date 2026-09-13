from collections.abc import Mapping, Sequence
from contextlib import asynccontextmanager
from typing import Any

from fastapi.testclient import TestClient

from app.api import get_service
from app.main import app
from app.schemas import SearchParams
from app.service import CardSearchService


class EmptyRepository:
    async def search_translation_oracle_ids(self, params: SearchParams) -> list[str]:
        return []

    async def search_oracle_cards(
        self, params: SearchParams, oracle_ids: Sequence[str] | None = None
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
