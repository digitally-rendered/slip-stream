"""MongoDB connection manager for slip-stream.

Provides async connection lifecycle management and a FastAPI-compatible
``get_database`` dependency.

Usage::

    from slip_stream.database import DatabaseManager

    db_manager = DatabaseManager(
        mongo_uri="mongodb://localhost:27017",
        database_name="my_app_db",
    )

    # In FastAPI lifespan:
    await db_manager.connect()
    yield
    await db_manager.close()

    # As a FastAPI dependency:
    app.dependency_overrides[get_database] = db_manager.get_database
"""

import os
from typing import Optional

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pymongo.errors import PyMongoError


class DatabaseManager:
    """Manages async MongoDB connections via Motor.

    Args:
        mongo_uri: MongoDB connection string. Defaults to ``MONGO_URI`` env var
            or ``mongodb://localhost:27017``.
        database_name: Database name. Defaults to ``DATABASE_NAME`` env var
            or ``slip_stream_db``.
    """

    def __init__(
        self,
        mongo_uri: Optional[str] = None,
        database_name: Optional[str] = None,
    ) -> None:
        self.mongo_uri = mongo_uri or os.getenv(
            "MONGO_URI", "mongodb://localhost:27017"
        )
        self.database_name = database_name or os.getenv(
            "DATABASE_NAME", "slip_stream_db"
        )
        self.client: Optional[AsyncIOMotorClient] = None
        self.db: Optional[AsyncIOMotorDatabase] = None

    async def connect(self) -> None:
        """Establish a connection to MongoDB and verify with a ping."""
        self.client = AsyncIOMotorClient(self.mongo_uri)
        self.db = self.client[self.database_name]
        try:
            await self.db.command("ping")
        except PyMongoError:
            raise

    async def close(self) -> None:
        """Close the MongoDB connection."""
        if self.client:
            self.client.close()
            self.client = None
            self.db = None

    def get_database(self) -> AsyncIOMotorDatabase:
        """Return the initialized AsyncIOMotorDatabase instance.

        Suitable for use as a FastAPI dependency.

        Raises:
            RuntimeError: If ``connect()`` has not been called.
        """
        if self.db is None:
            raise RuntimeError(
                "Database not initialized. Call connect() during application startup."
            )
        return self.db
