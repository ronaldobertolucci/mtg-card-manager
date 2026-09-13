from collections.abc import Mapping
from typing import Any

import pytest
from bson import ObjectId
from pydantic import ValidationError
from pymongo.errors import DuplicateKeyError

from app.translation_repository import TranslationDocument
from app.translation_schemas import (
    TranslationCreate,
    TranslationListParams,
    TranslationUpdate,
)
from app.translation_service import (
    InvalidTranslationIdError,
    OracleCardNotFoundError,
    TranslationConflictError,
    TranslationNotFoundError,
    TranslationService,
)


class FakeTranslationRepository:
    def __init__(self, card_exists: bool = True) -> None:
        self.card_exists = card_exists
        self.documents: dict[ObjectId, TranslationDocument] = {}
        self.raise_duplicate = False

    async def oracle_card_exists(self, oracle_id: str) -> bool:
        return self.card_exists

    async def create(self, document: TranslationDocument) -> TranslationDocument:
        if self.raise_duplicate:
            raise DuplicateKeyError("duplicate")
        translation_id = ObjectId()
        created = {**document, "_id": translation_id}
        self.documents[translation_id] = created
        return created

    async def get_by_id(self, translation_id: ObjectId) -> TranslationDocument | None:
        return self.documents.get(translation_id)

    async def get_by_card_language(
        self, oracle_id: str, lang: str
    ) -> TranslationDocument | None:
        return next(
            (
                document
                for document in self.documents.values()
                if document["oracle_id"] == oracle_id and document["lang"] == lang
            ),
            None,
        )

    async def list(
        self, filters: Mapping[str, Any], limit: int, offset: int
    ) -> list[TranslationDocument]:
        matches = [
            document
            for document in self.documents.values()
            if all(document.get(key) == value for key, value in filters.items())
        ]
        return matches[offset : offset + limit]

    async def update(
        self, translation_id: ObjectId, changes: Mapping[str, Any]
    ) -> TranslationDocument | None:
        document = self.documents.get(translation_id)
        if document is None:
            return None
        document.update(changes)
        return document

    async def delete(self, translation_id: ObjectId) -> bool:
        return self.documents.pop(translation_id, None) is not None


def translation_payload() -> TranslationCreate:
    return TranslationCreate(
        oracle_id="oracle-1",
        lang="pt-br",
        name="Raio",
        oracle_text="Raio causa 3 pontos de dano a qualquer alvo.",
        type_line="Mágica Instantânea",
    )


@pytest.mark.asyncio
async def test_translation_crud_flow() -> None:
    repository = FakeTranslationRepository()
    service = TranslationService(repository)

    created = await service.create(translation_payload())
    fetched = await service.get(created.id)
    by_card = await service.get_by_card_language("oracle-1", "pt-br")
    listed = await service.list(TranslationListParams(oracle_id="oracle-1", lang="pt-br"))
    updated = await service.update(created.id, TranslationUpdate(name="Raio atualizado"))
    await service.delete(created.id)

    assert created.lang == "pt-BR"
    assert fetched.id == created.id
    assert by_card.id == created.id
    assert [item.id for item in listed] == [created.id]
    assert updated.name == "Raio atualizado"
    with pytest.raises(TranslationNotFoundError):
        await service.get(created.id)


@pytest.mark.asyncio
async def test_create_requires_existing_oracle_card() -> None:
    service = TranslationService(FakeTranslationRepository(card_exists=False))

    with pytest.raises(OracleCardNotFoundError):
        await service.create(translation_payload())


@pytest.mark.asyncio
async def test_duplicate_translation_becomes_conflict() -> None:
    repository = FakeTranslationRepository()
    repository.raise_duplicate = True

    with pytest.raises(TranslationConflictError):
        await TranslationService(repository).create(translation_payload())


@pytest.mark.asyncio
async def test_invalid_object_id_is_rejected() -> None:
    with pytest.raises(InvalidTranslationIdError):
        await TranslationService(FakeTranslationRepository()).get("not-an-object-id")


def test_empty_update_and_english_translation_are_rejected() -> None:
    with pytest.raises(ValidationError):
        TranslationUpdate()
    with pytest.raises(ValidationError):
        TranslationCreate(oracle_id="oracle-1", lang="en", name="Bolt")
