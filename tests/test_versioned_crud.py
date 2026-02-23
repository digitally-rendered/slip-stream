"""Tests for VersionedMongoCRUD."""

import uuid

import pytest

from slip_stream.adapters.persistence.db.generic_crud import VersionedMongoCRUD
from slip_stream.core.schema.registry import SchemaRegistry


@pytest.fixture
def crud(registry, mock_db):
    """Create a VersionedMongoCRUD instance for the widget schema."""
    doc_model = registry.generate_document_model("widget")
    create_model = registry.generate_create_model("widget")
    update_model = registry.generate_update_model("widget")
    return VersionedMongoCRUD(
        db=mock_db,
        collection_name="widget",
        model=doc_model,
        create_model=create_model,
        update_model=update_model,
    )


@pytest.fixture
def create_model(registry):
    """Return the widget create model class."""
    return registry.generate_create_model("widget")


@pytest.fixture
def update_model(registry):
    """Return the widget update model class."""
    return registry.generate_update_model("widget")


class TestVersionedMongoCRUD:
    """Tests for CRUD operations with versioned documents."""

    async def test_create_returns_document_with_version_1(self, crud, create_model):
        """create() returns a document with record_version=1."""
        data = create_model(name="Blue Widget", color="blue", weight=42.0)
        result = await crud.create(data=data, user_id="user-1")

        assert result.record_version == 1
        assert result.entity_id is not None
        assert result.deleted_at is None
        assert result.created_by == "user-1"

    async def test_get_by_entity_id(self, crud, create_model):
        """get_by_entity_id() returns the created document."""
        data = create_model(name="Red Widget", color="red")
        created = await crud.create(data=data)

        result = await crud.get_by_entity_id(created.entity_id)
        assert result is not None
        assert result.entity_id == created.entity_id

    async def test_get_by_entity_id_not_found(self, crud):
        """get_by_entity_id() returns None for non-existent entity."""
        result = await crud.get_by_entity_id(uuid.uuid4())
        assert result is None

    async def test_update_creates_new_version(self, crud, create_model, update_model):
        """update_by_entity_id() creates a new document version."""
        data = create_model(name="Widget V1", color="green")
        created = await crud.create(data=data, user_id="user-1")

        update_data = update_model(color="yellow")
        updated = await crud.update_by_entity_id(
            entity_id=created.entity_id, data=update_data, user_id="user-2"
        )

        assert updated is not None
        assert updated.record_version == 2
        assert updated.entity_id == created.entity_id
        assert updated.updated_by == "user-2"

    async def test_update_not_found(self, crud, update_model):
        """update_by_entity_id() returns None for non-existent entity."""
        update_data = update_model(color="purple")
        result = await crud.update_by_entity_id(
            entity_id=uuid.uuid4(), data=update_data
        )
        assert result is None

    async def test_soft_delete(self, crud, create_model):
        """delete_by_entity_id() creates a tombstone version."""
        data = create_model(name="Doomed Widget")
        created = await crud.create(data=data, user_id="user-1")

        deleted = await crud.delete_by_entity_id(
            entity_id=created.entity_id, user_id="user-1"
        )
        assert deleted is not None
        assert deleted.deleted_at is not None
        assert deleted.record_version == 2
        assert deleted.deleted_by == "user-1"

    async def test_get_after_delete_returns_none(self, crud, create_model):
        """get_by_entity_id() returns None for soft-deleted entities."""
        data = create_model(name="Deleted Widget")
        created = await crud.create(data=data)

        await crud.delete_by_entity_id(entity_id=created.entity_id)

        result = await crud.get_by_entity_id(created.entity_id)
        assert result is None

    async def test_delete_not_found(self, crud):
        """delete_by_entity_id() returns None for non-existent entity."""
        result = await crud.delete_by_entity_id(entity_id=uuid.uuid4())
        assert result is None

    async def test_delete_already_deleted(self, crud, create_model):
        """delete_by_entity_id() returns None if already deleted."""
        data = create_model(name="Double Delete")
        created = await crud.create(data=data)

        await crud.delete_by_entity_id(entity_id=created.entity_id)
        result = await crud.delete_by_entity_id(entity_id=created.entity_id)
        assert result is None

    async def test_list_latest_active(self, crud, create_model):
        """list_latest_active() returns only the latest version of each entity."""
        data1 = create_model(name="Widget A")
        data2 = create_model(name="Widget B")
        await crud.create(data=data1)
        await crud.create(data=data2)

        results = await crud.list_latest_active()
        assert len(results) == 2

    async def test_list_excludes_deleted(self, crud, create_model):
        """list_latest_active() excludes soft-deleted entities."""
        data1 = create_model(name="Active Widget")
        data2 = create_model(name="Deleted Widget")
        await crud.create(data=data1)
        created2 = await crud.create(data=data2)

        await crud.delete_by_entity_id(entity_id=created2.entity_id)

        results = await crud.list_latest_active()
        assert len(results) == 1

    async def test_list_pagination(self, crud, create_model):
        """list_latest_active() supports skip and limit."""
        for i in range(5):
            await crud.create(data=create_model(name=f"Widget {i}"))

        results = await crud.list_latest_active(skip=0, limit=2)
        assert len(results) == 2

    async def test_update_no_changes(self, crud, create_model, update_model):
        """update_by_entity_id() returns None when no fields changed."""
        data = create_model(name="Unchanged", color="blue")
        created = await crud.create(data=data)

        # Update with the same value
        update_data = update_model(color="blue")
        result = await crud.update_by_entity_id(
            entity_id=created.entity_id, data=update_data
        )
        assert result is None

    async def test_create_with_explicit_entity_id(self, crud, create_model):
        """create() accepts an explicit entity_id."""
        explicit_id = uuid.uuid4()
        data = create_model(name="Explicit ID Widget")
        result = await crud.create(data=data, entity_id=explicit_id)

        assert result.entity_id == explicit_id
