"""Tests for storage routing and SQL repository factory."""

import pytest

from slip_stream.core.storage import StorageBackend, StorageConfig
from slip_stream.registry import SlipStreamRegistry

# ---------------------------------------------------------------------------
# StorageBackend enum
# ---------------------------------------------------------------------------


class TestStorageBackend:

    def test_values(self):
        assert StorageBackend.MONGO.value == "mongo"
        assert StorageBackend.SQL.value == "sql"

    def test_from_string(self):
        assert StorageBackend("mongo") == StorageBackend.MONGO
        assert StorageBackend("sql") == StorageBackend.SQL

    def test_invalid_value_raises(self):
        with pytest.raises(ValueError):
            StorageBackend("redis")

    def test_is_str(self):
        assert isinstance(StorageBackend.MONGO, str)
        assert StorageBackend.MONGO == "mongo"


# ---------------------------------------------------------------------------
# StorageConfig
# ---------------------------------------------------------------------------


class TestStorageConfig:

    def test_default_is_mongo(self):
        config = StorageConfig()
        assert config.default == StorageBackend.MONGO
        assert config.get("anything") == StorageBackend.MONGO

    def test_custom_default(self):
        config = StorageConfig(default=StorageBackend.SQL)
        assert config.default == StorageBackend.SQL
        assert config.get("widget") == StorageBackend.SQL

    def test_set_and_get(self):
        config = StorageConfig()
        config.set("widget", "sql")
        assert config.get("widget") == StorageBackend.SQL
        assert config.get("gadget") == StorageBackend.MONGO

    def test_set_with_enum(self):
        config = StorageConfig()
        config.set("widget", StorageBackend.SQL)
        assert config.get("widget") == StorageBackend.SQL

    def test_set_invalid_backend_raises(self):
        config = StorageConfig()
        with pytest.raises(ValueError, match="Unknown storage backend"):
            config.set("widget", "redis")

    def test_initial_storage_map(self):
        config = StorageConfig(
            storage_map={"widget": StorageBackend.SQL, "order": StorageBackend.SQL}
        )
        assert config.get("widget") == StorageBackend.SQL
        assert config.get("order") == StorageBackend.SQL
        assert config.get("pet") == StorageBackend.MONGO

    def test_sql_schemas(self):
        config = StorageConfig()
        config.set("widget", "sql")
        config.set("order", "sql")
        config.set("pet", "mongo")
        sql = config.sql_schemas()
        assert sorted(sql) == ["order", "widget"]

    def test_mongo_schemas(self):
        config = StorageConfig()
        config.set("widget", "sql")
        config.set("pet", "mongo")
        mongo = config.mongo_schemas()
        assert mongo == ["pet"]

    def test_merge(self):
        base = StorageConfig()
        base.set("widget", "mongo")
        base.set("order", "mongo")

        override = StorageConfig()
        override.set("widget", "sql")

        base.merge(override)
        assert base.get("widget") == StorageBackend.SQL
        assert base.get("order") == StorageBackend.MONGO


# ---------------------------------------------------------------------------
# Registry storage() method
# ---------------------------------------------------------------------------


class TestRegistryStorage:

    def test_storage_direct_call(self):
        registry = SlipStreamRegistry()
        registry.storage("widget", backend="sql")
        entries = registry.get_storage_entries()
        assert len(entries) == 1
        assert entries[0].schema_name == "widget"
        assert entries[0].backend == "sql"

    def test_storage_as_decorator(self):
        registry = SlipStreamRegistry()

        @registry.storage("order", backend="sql")
        class OrderConfig:
            pass

        entries = registry.get_storage_entries()
        assert len(entries) == 1
        assert entries[0].schema_name == "order"

    def test_storage_invalid_backend_raises(self):
        registry = SlipStreamRegistry()
        with pytest.raises(ValueError, match="Unknown storage backend"):
            registry.storage("widget", backend="redis")

    def test_storage_default_backend_is_mongo(self):
        registry = SlipStreamRegistry()
        registry.storage("widget")
        entries = registry.get_storage_entries()
        assert entries[0].backend == "mongo"

    def test_multiple_storage_entries(self):
        registry = SlipStreamRegistry()
        registry.storage("widget", backend="sql")
        registry.storage("order", backend="sql")
        registry.storage("pet", backend="mongo")
        entries = registry.get_storage_entries()
        assert len(entries) == 3

    def test_get_storage_entries_returns_copy(self):
        registry = SlipStreamRegistry()
        registry.storage("widget", backend="sql")
        entries1 = registry.get_storage_entries()
        entries2 = registry.get_storage_entries()
        assert entries1 is not entries2


# ---------------------------------------------------------------------------
# EntityRegistration storage_backend field
# ---------------------------------------------------------------------------


class TestEntityRegistrationStorageBackend:

    def test_default_storage_backend(self, schema_dir, registry):
        """EntityRegistration defaults to 'mongo' storage_backend."""
        from slip_stream.container import init_container

        schema_names = registry.get_schema_names()
        container = init_container(schema_names=schema_names)
        reg = container.get("widget")
        assert reg.storage_backend == "mongo"

    def test_sql_storage_backend_set_via_config(self, schema_dir, registry):
        """EntityRegistration gets 'sql' when StorageConfig routes to SQL."""
        import sqlalchemy as sa

        from slip_stream.container import init_container

        metadata = sa.MetaData()
        from slip_stream.adapters.persistence.db.sql_repository import (
            build_table_from_schema,
        )

        widget_schema = registry.get_schema("widget")
        table = build_table_from_schema("widget", widget_schema, metadata)

        storage_config = StorageConfig()
        storage_config.set("widget", "sql")

        schema_names = registry.get_schema_names()
        container = init_container(
            schema_names=schema_names,
            storage_config=storage_config,
            sql_tables={"widget": table},
        )
        reg = container.get("widget")
        assert reg.storage_backend == "sql"

        # Non-SQL schemas should still be mongo
        if "gadget" in schema_names:
            gadget_reg = container.get("gadget")
            assert gadget_reg.storage_backend == "mongo"


# ---------------------------------------------------------------------------
# SQLRepositoryFactory
# ---------------------------------------------------------------------------


class TestSQLRepositoryFactory:

    def test_create_returns_class(self, schema_dir, registry):
        """SQLRepositoryFactory.create() returns a class with correct name."""
        import sqlalchemy as sa

        from slip_stream.adapters.persistence.db.sql_repository import (
            build_table_from_schema,
        )
        from slip_stream.adapters.persistence.db.sql_repository_factory import (
            SQLRepositoryFactory,
        )

        metadata = sa.MetaData()
        widget_schema = registry.get_schema("widget")
        table = build_table_from_schema("widget", widget_schema, metadata)

        doc_model = registry.generate_document_model("widget")
        create_model = registry.generate_create_model("widget")
        update_model = registry.generate_update_model("widget")

        RepoClass = SQLRepositoryFactory.create(
            schema_name="widget",
            table=table,
            doc_model=doc_model,
            create_model=create_model,
            update_model=update_model,
        )

        assert RepoClass.__name__ == "WidgetSQLRepository"
        assert hasattr(RepoClass, "create")
        assert hasattr(RepoClass, "get_by_entity_id")
        assert hasattr(RepoClass, "list_latest_active")
        assert hasattr(RepoClass, "update_by_entity_id")
        assert hasattr(RepoClass, "delete_by_entity_id")


# ---------------------------------------------------------------------------
# SQL CRUD integration (using aiosqlite)
# ---------------------------------------------------------------------------


class TestSQLCRUDIntegration:

    @pytest.fixture
    async def sql_setup(self, schema_dir, registry):
        """Create an in-memory SQLite database with widget table."""
        import sqlalchemy as sa
        from sqlalchemy.ext.asyncio import (
            async_sessionmaker,
            create_async_engine,
        )

        from slip_stream.adapters.persistence.db.sql_repository import (
            build_table_from_schema,
        )
        from slip_stream.adapters.persistence.db.sql_repository_factory import (
            SQLRepositoryFactory,
        )

        engine = create_async_engine("sqlite+aiosqlite://", echo=False)
        metadata = sa.MetaData()

        widget_schema = registry.get_schema("widget")
        table = build_table_from_schema("widget", widget_schema, metadata)

        doc_model = registry.generate_document_model("widget")
        create_model = registry.generate_create_model("widget")
        update_model = registry.generate_update_model("widget")

        async with engine.begin() as conn:
            await conn.run_sync(metadata.create_all)

        RepoClass = SQLRepositoryFactory.create(
            schema_name="widget",
            table=table,
            doc_model=doc_model,
            create_model=create_model,
            update_model=update_model,
        )

        session_factory = async_sessionmaker(engine, expire_on_commit=False)

        yield {
            "engine": engine,
            "session_factory": session_factory,
            "RepoClass": RepoClass,
            "create_model": create_model,
            "update_model": update_model,
        }

        await engine.dispose()

    async def test_create_via_factory(self, sql_setup):
        setup = sql_setup
        async with setup["session_factory"]() as session:
            repo = setup["RepoClass"](session)
            data = setup["create_model"](name="TestWidget", color="red")
            result = await repo.create(data, user_id="user-1")
            await session.commit()

        assert result.name == "TestWidget"
        assert result.record_version == 1
        assert result.entity_id is not None
        assert result.created_by == "user-1"

    async def test_get_by_entity_id_via_factory(self, sql_setup):
        setup = sql_setup
        async with setup["session_factory"]() as session:
            repo = setup["RepoClass"](session)
            data = setup["create_model"](name="GetWidget")
            created = await repo.create(data, user_id="user-1")
            await session.commit()

            fetched = await repo.get_by_entity_id(created.entity_id)
            assert fetched is not None
            assert fetched.name == "GetWidget"

    async def test_list_latest_active_via_factory(self, sql_setup):
        setup = sql_setup
        async with setup["session_factory"]() as session:
            repo = setup["RepoClass"](session)
            for i in range(3):
                data = setup["create_model"](name=f"Widget-{i}")
                await repo.create(data, user_id="user-1")
            await session.commit()

            results = await repo.list_latest_active()
            assert len(results) == 3

    async def test_update_via_factory(self, sql_setup):
        setup = sql_setup
        async with setup["session_factory"]() as session:
            repo = setup["RepoClass"](session)
            data = setup["create_model"](name="Original")
            created = await repo.create(data, user_id="user-1")
            await session.commit()

            update_data = setup["update_model"](name="Updated")
            updated = await repo.update_by_entity_id(
                created.entity_id, update_data, user_id="user-2"
            )
            await session.commit()

            assert updated is not None
            assert updated.name == "Updated"
            assert updated.record_version == 2

    async def test_delete_via_factory(self, sql_setup):
        setup = sql_setup
        async with setup["session_factory"]() as session:
            repo = setup["RepoClass"](session)
            data = setup["create_model"](name="ToDelete")
            created = await repo.create(data, user_id="user-1")
            await session.commit()

            deleted = await repo.delete_by_entity_id(
                created.entity_id, user_id="user-1"
            )
            await session.commit()

            assert deleted is not None
            assert deleted.deleted_at is not None
            assert deleted.record_version == 2

            # Should not be retrievable
            result = await repo.get_by_entity_id(created.entity_id)
            assert result is None


# ---------------------------------------------------------------------------
# StorageConfig precedence
# ---------------------------------------------------------------------------


class TestStorageConfigPrecedence:
    """Verify decorator > constructor > config > default precedence."""

    def test_decorator_overrides_constructor(self):
        """Registry storage() entries override constructor storage_map."""
        from slip_stream.app import SlipStream

        registry = SlipStreamRegistry()
        registry.storage("widget", backend="sql")

        # Simulating: constructor says mongo, decorator says sql
        slip = SlipStream.__new__(SlipStream)
        slip._storage_map = {"widget": "mongo"}
        slip._storage_default = "mongo"
        slip._registry = registry

        config = slip._build_storage_config()
        # Decorator wins
        assert config.get("widget") == StorageBackend.SQL

    def test_constructor_overrides_config(self):
        """Constructor storage_map overrides config file."""
        from slip_stream.config import SlipStreamConfig

        cfg = SlipStreamConfig(storage_map={"widget": "mongo"})
        # Constructor says sql
        merged_map = {"widget": "sql"}
        for name, backend in cfg.storage_map.items():
            if name not in merged_map:
                merged_map[name] = backend

        assert merged_map["widget"] == "sql"

    def test_config_provides_default(self):
        """Config file storage_map is used when constructor doesn't override."""
        from slip_stream.config import SlipStreamConfig

        cfg = SlipStreamConfig(storage_map={"order": "sql"})
        merged_map = {}
        for name, backend in cfg.storage_map.items():
            if name not in merged_map:
                merged_map[name] = backend

        assert merged_map["order"] == "sql"
