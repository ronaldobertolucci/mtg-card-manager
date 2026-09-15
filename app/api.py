from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, status

from app.database import get_database
from app.repository import MongoCardRepository
from app.schemas import CardResponse, SearchParams
from app.service import CardSearchService
from app.translation_schemas import LANGUAGE_PATTERN

router = APIRouter(prefix="/cards", tags=["cards"])


def get_service(request: Request) -> CardSearchService:
    return CardSearchService(MongoCardRepository(get_database(request.app)))


@router.get("/search", response_model=list[CardResponse])
async def search_cards(
    query: Annotated[SearchParams, Query()],
    service: Annotated[CardSearchService, Depends(get_service)],
) -> list[CardResponse]:
    cards = await service.search(query)
    if not cards:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No cards found")
    return cards


@router.get("/{oracle_id}", response_model=CardResponse)
async def get_card_by_oracle_id(
    oracle_id: Annotated[str, Path(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9-]+$")],
    service: Annotated[CardSearchService, Depends(get_service)],
    lang: Annotated[str, Query(min_length=2, max_length=16, pattern=LANGUAGE_PATTERN)] = "pt-BR",
) -> CardResponse:
    card = await service.get_by_oracle_id(oracle_id, lang)
    if card is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Card not found")
    return card
