from datetime import UTC, datetime

from bson import ObjectId
from bson.errors import InvalidId
from pymongo.errors import DuplicateKeyError

from app.translation_repository import TranslationDocument, TranslationRepository
from app.translation_schemas import (
    TranslationCreate,
    TranslationListParams,
    TranslationResponse,
    TranslationUpdate,
    normalize_language,
)


class InvalidTranslationIdError(ValueError):
    pass


class TranslationNotFoundError(LookupError):
    pass


class OracleCardNotFoundError(LookupError):
    pass


class TranslationConflictError(RuntimeError):
    pass


class InvalidTranslationStructureError(ValueError):
    pass


class TranslationService:
    def __init__(self, repository: TranslationRepository) -> None:
        self._repository = repository

    async def create(self, payload: TranslationCreate) -> TranslationResponse:
        fields = await self._validate_structure(payload.model_dump(exclude_unset=True))

        now = datetime.now(UTC)
        document: TranslationDocument = {
            **fields,
            "created_at": now,
            "updated_at": now,
        }
        try:
            created = await self._repository.create(document)
        except DuplicateKeyError as error:
            raise TranslationConflictError from error
        return self._serialize(created)

    async def get(self, translation_id: str) -> TranslationResponse:
        document = await self._repository.get_by_id(self._parse_id(translation_id))
        if document is None:
            raise TranslationNotFoundError(translation_id)
        return self._serialize(document)

    async def get_by_card_language(self, oracle_id: str, lang: str) -> TranslationResponse:
        document = await self._repository.get_by_card_language(oracle_id, normalize_language(lang))
        if document is None:
            raise TranslationNotFoundError(f"{oracle_id}/{lang}")
        return self._serialize(document)

    async def list(self, params: TranslationListParams) -> list[TranslationResponse]:
        filters: dict[str, str] = {}
        if params.oracle_id is not None:
            filters["oracle_id"] = params.oracle_id
        if params.lang is not None:
            filters["lang"] = params.lang
        documents = await self._repository.list(filters, params.limit, params.offset)
        return [self._serialize(document) for document in documents]

    async def update(self, translation_id: str, payload: TranslationUpdate) -> TranslationResponse:
        changes = payload.model_dump(exclude_unset=True)
        parsed_id = self._parse_id(translation_id)
        existing = await self._repository.get_by_id(parsed_id)
        if existing is None:
            raise TranslationNotFoundError(translation_id)
        if "card_faces" in changes and changes["card_faces"] is None:
            raise InvalidTranslationStructureError("card_faces cannot be null in an update")
        # Root text is derived for multiface cards, never edited independently.
        if existing.get("card_faces") and any(
            field in changes for field in ("name", "oracle_text", "type_line", "flavor_text")
        ):
            raise InvalidTranslationStructureError("Edit card_faces for multiface translations")
        validated = await self._validate_structure({**existing, **changes}, updating=True)
        changes.update(
            {
                key: validated[key]
                for key in ("name", "oracle_text", "type_line", "flavor_text", "card_faces")
            }
        )
        changes["updated_at"] = datetime.now(UTC)
        document = await self._repository.update(parsed_id, changes)
        if document is None:
            raise TranslationNotFoundError(translation_id)
        return self._serialize(document)

    async def _validate_structure(
        self, document: TranslationDocument, updating: bool = False
    ) -> TranslationDocument:
        card = await self._repository.get_oracle_card(document["oracle_id"])
        if card is None:
            raise OracleCardNotFoundError(document["oracle_id"])
        original_faces = card.get("card_faces") or []
        faces = document.get("card_faces")
        text_fields = ("name", "oracle_text", "type_line", "flavor_text")
        if len(original_faces) >= 2:
            if not faces:
                raise InvalidTranslationStructureError("All card faces must be translated")
            indices = [face["face_index"] for face in faces]
            if sorted(indices) != list(range(len(original_faces))):
                raise InvalidTranslationStructureError(
                    "Face indices must cover every original face exactly once"
                )
            if not updating and any(field in document for field in text_fields):
                raise InvalidTranslationStructureError(
                    "Provide text inside card_faces for multiface translations"
                )
            faces = sorted(faces, key=lambda face: face["face_index"])
            return {
                **document,
                "card_faces": faces,
                "name": " // ".join(face["name"] for face in faces),
                "oracle_text": None,
                "type_line": None,
                "flavor_text": None,
            }
        if "card_faces" in document and (not updating or faces is not None):
            raise InvalidTranslationStructureError("Single-face cards cannot have card_faces")
        if not document.get("name"):
            raise InvalidTranslationStructureError("Single-face cards require a translated name")
        return {
            "oracle_text": None,
            "type_line": None,
            "flavor_text": None,
            "card_faces": None,
            **document,
        }

    async def delete(self, translation_id: str) -> None:
        deleted = await self._repository.delete(self._parse_id(translation_id))
        if not deleted:
            raise TranslationNotFoundError(translation_id)

    @staticmethod
    def _parse_id(value: str) -> ObjectId:
        try:
            return ObjectId(value)
        except (InvalidId, TypeError) as error:
            raise InvalidTranslationIdError(value) from error

    @staticmethod
    def _serialize(document: TranslationDocument) -> TranslationResponse:
        payload = dict(document)
        payload["id"] = str(payload.pop("_id"))
        return TranslationResponse.model_validate(payload)
