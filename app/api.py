from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.database import get_database
from app.repository import MongoCardRepository
from app.schemas import CardResponse, SearchParams
from app.service import CardSearchService

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

