from typing import Any

from fastapi import FastAPI
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from ..core.config import settings


MONGO_CLIENT_KEY = "mongo_client"
MONGO_DB_KEY = "mongo_db"


async def connect_to_mongo(app: FastAPI) -> None:
    uri = settings.MONGO_URI
    client = AsyncIOMotorClient(uri)
    db_name = uri.rsplit("/", 1)[-1]

    app.state.__setattr__(MONGO_CLIENT_KEY, client)
    app.state.__setattr__(MONGO_DB_KEY, client[db_name])


async def close_mongo_connection(app: FastAPI) -> None:
    client: AsyncIOMotorClient | None = getattr(app.state, MONGO_CLIENT_KEY, None)
    if client:
        client.close()


def get_database(app: FastAPI) -> AsyncIOMotorDatabase:
    return getattr(app.state, MONGO_DB_KEY)
