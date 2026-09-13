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


class TranslationService:
    def __init__(self, repository: TranslationRepository) -> None:
        self._repository = repository

    async def create(self, payload: TranslationCreate) -> TranslationResponse:
        if not await self._repository.oracle_card_exists(payload.oracle_id):
            raise OracleCardNotFoundError(payload.oracle_id)

        now = datetime.now(UTC)
        document: TranslationDocument = {
            **payload.model_dump(),
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
        document = await self._repository.get_by_card_language(
            oracle_id, normalize_language(lang)
        )
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

    async def update(
        self, translation_id: str, payload: TranslationUpdate
    ) -> TranslationResponse:
        changes = payload.model_dump(exclude_unset=True)
        changes["updated_at"] = datetime.now(UTC)
        document = await self._repository.update(self._parse_id(translation_id), changes)
        if document is None:
            raise TranslationNotFoundError(translation_id)
        return self._serialize(document)

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

