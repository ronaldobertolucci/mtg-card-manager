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
        self.card_faces = None

    async def get_oracle_card(self, oracle_id: str) -> TranslationDocument | None:
        return {"card_faces": self.card_faces} if self.card_exists else None

    async def create(self, document: TranslationDocument) -> TranslationDocument:
        if self.raise_duplicate:
            raise DuplicateKeyError("duplicate")
        translation_id = ObjectId()
        created = {**document, "_id": translation_id}
        self.documents[translation_id] = created
        return created

    async def get_by_id(self, translation_id: ObjectId) -> TranslationDocument | None:
        return self.documents.get(translation_id)

    async def get_by_card_language(self, oracle_id: str, lang: str) -> TranslationDocument | None:
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


def multiface_payload(**overrides):
    return TranslationCreate(
        **{
            "oracle_id": "oracle-1",
            "lang": "pt-BR",
            "card_faces": [
                {"face_index": 1, "name": "Verso"},
                {"face_index": 0, "name": "Frente", "oracle_text": "Texto"},
            ],
            **overrides,
        }
    )


@pytest.mark.asyncio
async def test_multiface_crud_replaces_faces_and_derives_name():
    repository = FakeTranslationRepository()
    repository.card_faces = [{"name": "Front"}, {"name": "Back"}]
    service = TranslationService(repository)
    created = await service.create(multiface_payload())
    assert created.name == "Frente // Verso"
    assert [face.face_index for face in created.card_faces] == [0, 1]
    assert (await service.get(created.id)).card_faces == created.card_faces
    assert (
        await service.get_by_card_language("oracle-1", "pt-br")
    ).card_faces == created.card_faces
    assert (await service.list(TranslationListParams()))[0].card_faces == created.card_faces
    updated = await service.update(
        created.id,
        TranslationUpdate(
            card_faces=[
                {"face_index": 0, "name": "Nova frente"},
                {"face_index": 1, "name": "Novo verso"},
            ]
        ),
    )
    assert updated.name == "Nova frente // Novo verso"
    assert updated.card_faces[0].oracle_text is None
    await service.delete(created.id)
    with pytest.raises(TranslationNotFoundError):
        await service.get(created.id)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "faces",
    [
        None,
        [{"face_index": 0, "name": "Frente"}, {"face_index": 0, "name": "Repetida"}],
        [{"face_index": 0, "name": "Frente"}, {"face_index": 2, "name": "Inválida"}],
        [
            {"face_index": 0, "name": "Frente"},
            {"face_index": 1, "name": "Verso"},
            {"face_index": 2, "name": "Extra"},
        ],
    ],
)
async def test_multiface_create_and_patch_reject_invalid_coverage(faces):
    from app.translation_service import InvalidTranslationStructureError

    repository = FakeTranslationRepository()
    repository.card_faces = [{}, {}]
    service = TranslationService(repository)
    with pytest.raises(InvalidTranslationStructureError):
        await service.create(multiface_payload(card_faces=faces))
    assert not repository.documents
    created = await service.create(multiface_payload())
    with pytest.raises(InvalidTranslationStructureError):
        await service.update(created.id, TranslationUpdate(card_faces=faces))
    assert (await service.get(created.id)) == created


@pytest.mark.asyncio
async def test_single_face_rejects_faces_and_requires_name():
    from app.translation_service import InvalidTranslationStructureError

    repository = FakeTranslationRepository()
    service = TranslationService(repository)
    for payload in [multiface_payload(), TranslationCreate(oracle_id="oracle-1", lang="pt")]:
        with pytest.raises(InvalidTranslationStructureError):
            await service.create(payload)
    created = await service.create(translation_payload())
    with pytest.raises(InvalidTranslationStructureError):
        await service.update(
            created.id, TranslationUpdate(card_faces=multiface_payload().card_faces)
        )
    assert (await service.get(created.id)) == created


@pytest.mark.asyncio
async def test_multiface_root_text_rejected_and_patch_requires_existing_card():
    from app.translation_service import InvalidTranslationStructureError

    repository = FakeTranslationRepository()
    repository.card_faces = [{}, {}]
    service = TranslationService(repository)
    with pytest.raises(InvalidTranslationStructureError):
        await service.create(multiface_payload(name="Duplicado"))
    created = await service.create(multiface_payload())
    with pytest.raises(InvalidTranslationStructureError):
        await service.update(created.id, TranslationUpdate(name="Duplicado"))
    repository.card_exists = False
    with pytest.raises(OracleCardNotFoundError):
        await service.update(created.id, TranslationUpdate(card_faces=created.card_faces))


@pytest.mark.parametrize(
    "face",
    [
        {"face_index": -1, "name": "Nome"},
        {"face_index": 0, "name": " "},
        {"face_index": 0},
        {"face_index": 0, "name": "Nome", "mana_cost": "{U}"},
        {"face_index": True, "name": "Nome"},
    ],
)
def test_face_schema_rejects_invalid_fields(face):
    from app.translation_schemas import TranslationFace

    with pytest.raises(ValidationError):
        TranslationFace(**face)


def test_face_list_requires_at_least_two_faces():
    with pytest.raises(ValidationError):
        multiface_payload(card_faces=[{"face_index": 0, "name": "Frente"}])
