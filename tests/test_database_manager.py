"""Tests for slip_stream/database.py — DatabaseManager."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from mongomock_motor import AsyncMongoMockClient
from pymongo.errors import PyMongoError

from slip_stream.database import DatabaseManager


class TestGetDatabaseBeforeConnect:
    """get_database() must raise RuntimeError when connect() has not been called."""

    def test_get_database_before_connect_raises(self):
        """Calling get_database() on a fresh manager raises RuntimeError."""
        manager = DatabaseManager(
            mongo_uri="mongodb://localhost:27017",
            database_name="test_db",
        )

        with pytest.raises(RuntimeError, match="Database not initialized"):
            manager.get_database()


class TestConnectPingFailure:
    """connect() must reset client and db to None when ping fails."""

    async def test_connect_ping_failure(self):
        """If ping raises PyMongoError, client and db are reset to None and error propagates."""
        manager = DatabaseManager(
            mongo_uri="mongodb://localhost:27017",
            database_name="test_db",
        )

        mock_db = MagicMock()
        mock_db.command = AsyncMock(side_effect=PyMongoError("connection refused"))

        mock_client = MagicMock()
        mock_client.__getitem__ = MagicMock(return_value=mock_db)

        with patch(
            "slip_stream.database.AsyncIOMotorClient",
            return_value=mock_client,
        ):
            with pytest.raises(PyMongoError):
                await manager.connect()

        assert manager.client is None
        assert manager.db is None


class TestConnectAndCloseLifecycle:
    """connect() and close() transition the manager through initialized and uninitialized states."""

    async def test_connect_and_close_lifecycle(self):
        """After connect(), get_database() returns the db; after close(), manager is reset."""
        manager = DatabaseManager(
            mongo_uri="mongodb://localhost:27017",
            database_name="test_lifecycle_db",
        )

        mock_client = AsyncMongoMockClient()

        with patch(
            "slip_stream.database.AsyncIOMotorClient",
            return_value=mock_client,
        ):
            await manager.connect()

        # After connect(), database is accessible
        db = manager.get_database()
        assert db is not None

        await manager.close()

        # After close(), client and db are None
        assert manager.client is None
        assert manager.db is None

        # get_database() should raise again
        with pytest.raises(RuntimeError, match="Database not initialized"):
            manager.get_database()


class TestEnvVarDefaults:
    """DatabaseManager reads MONGO_URI and DATABASE_NAME from environment variables."""

    def test_env_var_defaults(self, monkeypatch):
        """MONGO_URI and DATABASE_NAME env vars are used when constructor args are omitted."""
        monkeypatch.setenv("MONGO_URI", "mongodb://envhost:27017")
        monkeypatch.setenv("DATABASE_NAME", "env_db_name")

        manager = DatabaseManager()

        assert manager.mongo_uri == "mongodb://envhost:27017"
        assert manager.database_name == "env_db_name"

    def test_env_var_fallback_defaults(self, monkeypatch):
        """When env vars are absent, hardcoded defaults are used."""
        monkeypatch.delenv("MONGO_URI", raising=False)
        monkeypatch.delenv("DATABASE_NAME", raising=False)

        manager = DatabaseManager()

        assert manager.mongo_uri == "mongodb://localhost:27017"
        assert manager.database_name == "slip_stream_db"

    def test_constructor_args_take_precedence_over_env(self, monkeypatch):
        """Explicit constructor args override env vars."""
        monkeypatch.setenv("MONGO_URI", "mongodb://envhost:27017")
        monkeypatch.setenv("DATABASE_NAME", "env_db_name")

        manager = DatabaseManager(
            mongo_uri="mongodb://explicit:27017",
            database_name="explicit_db",
        )

        assert manager.mongo_uri == "mongodb://explicit:27017"
        assert manager.database_name == "explicit_db"
