from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest
from bson import ObjectId
from fastapi.testclient import TestClient

from app.main import app
from app.translation_api import get_translation_service
from app.translation_service import TranslationService


@asynccontextmanager
async def no_database_lifespan(application):
    yield


@pytest.fixture
def translation_client():
    repository = AsyncMock()
    repository.get_oracle_card.return_value = {"card_faces": [{}, {}]}
    repository.create.side_effect = lambda document: {**document, "_id": ObjectId()}
    original_lifespan = app.router.lifespan_context
    app.router.lifespan_context = no_database_lifespan
    app.dependency_overrides[get_translation_service] = lambda: TranslationService(repository)
    try:
        with TestClient(app) as client:
            yield client, repository
    finally:
        app.dependency_overrides.clear()
        app.router.lifespan_context = original_lifespan


def test_multiface_create_response_and_invalid_patch(translation_client):
    client, repository = translation_client
    payload = {
        "oracle_id": "oracle-1",
        "lang": "pt-BR",
        "card_faces": [{"face_index": 0, "name": "Frente"}, {"face_index": 1, "name": "Verso"}],
    }
    response = client.post("/translations", json=payload)
    assert response.status_code == 201
    document = response.json()
    assert document["name"] == "Frente // Verso"
    assert document["card_faces"][0]["oracle_text"] is None
    repository.get_by_id.return_value = {**document, "_id": ObjectId(document["id"])}
    response = client.patch(f"/translations/{document['id']}", json={"card_faces": None})
    assert response.status_code == 422
    repository.update.assert_not_awaited()


@pytest.mark.parametrize(
    "card,payload",
    [
        (
            {},
            {
                "name": "Nome",
                "card_faces": [
                    {"face_index": 0, "name": "Frente"},
                    {"face_index": 1, "name": "Verso"},
                ],
            },
        ),
        ({"card_faces": [{}, {}]}, {"name": "Nome"}),
        (
            {"card_faces": [{}, {}]},
            {
                "card_faces": [
                    {"face_index": 0, "name": "Frente"},
                    {"face_index": 0, "name": "Verso"},
                ]
            },
        ),
    ],
)
def test_invalid_card_structure_returns_422(translation_client, card, payload):
    client, repository = translation_client
    repository.get_oracle_card.return_value = card
    response = client.post(
        "/translations", json={"oracle_id": "oracle-1", "lang": "pt-BR", **payload}
    )
    assert response.status_code == 422
    repository.create.assert_not_awaited()
