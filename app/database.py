from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from app.config import get_settings


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    client: AsyncIOMotorClient = AsyncIOMotorClient(settings.mongodb_uri)
    database = client[settings.mongodb_database]
    await database.oracle_cards.create_index(
        [("oracle_id", 1)],
        unique=True,
        name="uq_oracle_cards_oracle_id",
    )
    await database.translations.create_index(
        [("oracle_id", 1), ("lang", 1)],
        unique=True,
        name="uq_translation_oracle_lang",
    )
    await database.translations.create_index(
        [("lang", 1)],
        name="ix_translation_lang",
    )
    app.state.mongo_client = client
    app.state.database = database
    yield
    client.close()


def get_database(app: FastAPI) -> AsyncIOMotorDatabase:
    return app.state.database
