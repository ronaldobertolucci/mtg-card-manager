from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response, status

from app.database import get_database
from app.translation_repository import MongoTranslationRepository
from app.translation_schemas import (
    LANGUAGE_PATTERN,
    TranslationCreate,
    TranslationListParams,
    TranslationResponse,
    TranslationUpdate,
)
from app.translation_service import (
    InvalidTranslationIdError,
    OracleCardNotFoundError,
    TranslationConflictError,
    TranslationNotFoundError,
    TranslationService,
)

router = APIRouter(prefix="/translations", tags=["translations"])


def get_translation_service(request: Request) -> TranslationService:
    database = get_database(request.app)
    return TranslationService(MongoTranslationRepository(database))


def _translate_error(error: Exception) -> HTTPException:
    if isinstance(error, InvalidTranslationIdError):
        return HTTPException(status_code=400, detail="Invalid translation id")
    if isinstance(error, OracleCardNotFoundError):
        return HTTPException(status_code=404, detail="Oracle card not found")
    if isinstance(error, TranslationNotFoundError):
        return HTTPException(status_code=404, detail="Translation not found")
    if isinstance(error, TranslationConflictError):
        return HTTPException(
            status_code=409,
            detail="A translation for this oracle_id and lang already exists",
        )
    raise error


@router.post("", response_model=TranslationResponse, status_code=status.HTTP_201_CREATED)
async def create_translation(
    payload: TranslationCreate,
    service: Annotated[TranslationService, Depends(get_translation_service)],
) -> TranslationResponse:
    try:
        return await service.create(payload)
    except (OracleCardNotFoundError, TranslationConflictError) as error:
        raise _translate_error(error) from error


@router.get("", response_model=list[TranslationResponse])
async def list_translations(
    query: Annotated[TranslationListParams, Query()],
    service: Annotated[TranslationService, Depends(get_translation_service)],
) -> list[TranslationResponse]:
    return await service.list(query)


@router.get("/by-card/{oracle_id}/{lang}", response_model=TranslationResponse)
async def get_translation_by_card_language(
    oracle_id: Annotated[
        str,
        Path(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9-]+$"),
    ],
    lang: Annotated[str, Path(min_length=2, max_length=16, pattern=LANGUAGE_PATTERN)],
    service: Annotated[TranslationService, Depends(get_translation_service)],
) -> TranslationResponse:
    try:
        return await service.get_by_card_language(oracle_id, lang)
    except TranslationNotFoundError as error:
        raise _translate_error(error) from error


@router.get("/{translation_id}", response_model=TranslationResponse)
async def get_translation(
    translation_id: str,
    service: Annotated[TranslationService, Depends(get_translation_service)],
) -> TranslationResponse:
    try:
        return await service.get(translation_id)
    except (InvalidTranslationIdError, TranslationNotFoundError) as error:
        raise _translate_error(error) from error


@router.patch("/{translation_id}", response_model=TranslationResponse)
async def update_translation(
    translation_id: str,
    payload: TranslationUpdate,
    service: Annotated[TranslationService, Depends(get_translation_service)],
) -> TranslationResponse:
    try:
        return await service.update(translation_id, payload)
    except (InvalidTranslationIdError, TranslationNotFoundError) as error:
        raise _translate_error(error) from error


@router.delete("/{translation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_translation(
    translation_id: str,
    service: Annotated[TranslationService, Depends(get_translation_service)],
) -> Response:
    try:
        await service.delete(translation_id)
    except (InvalidTranslationIdError, TranslationNotFoundError) as error:
        raise _translate_error(error) from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)
