import asyncio
from collections.abc import Sequence

import httpx
from pydantic import ValidationError

from app.schemas import ResolvedCardResponse


class ScryfallCardNotFound(Exception):
    pass


class ScryfallUnavailable(Exception):
    pass


class ScryfallClient:
    async def get_by_ids(self, ids: Sequence[str]) -> dict[str, ResolvedCardResponse]:
        cards: dict[str, ResolvedCardResponse] = {}
        async with httpx.AsyncClient(
            base_url="https://api.scryfall.com",
            timeout=10.0,
            headers={"User-Agent": "mtg-card-manager/0.1.0", "Accept": "application/json"},
        ) as client:
            for index, card_id in enumerate(ids):
                if index:
                    await asyncio.sleep(0.1)
                try:
                    response = await client.get(f"/cards/{card_id}")
                    if response.status_code == 404:
                        raise ScryfallCardNotFound(card_id)
                    response.raise_for_status()
                    card = ResolvedCardResponse.model_validate(response.json())
                    if card.id != card_id:
                        raise ValueError("Scryfall returned a different card ID")
                except (httpx.HTTPError, ValidationError, ValueError) as exc:
                    raise ScryfallUnavailable("Unable to resolve card from Scryfall") from exc
                cards[card_id] = card
        return cards
