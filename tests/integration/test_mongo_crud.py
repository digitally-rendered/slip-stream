"""Integration tests for VersionedMongoCRUD against real MongoDB.

Validates that the append-only versioned document pattern works correctly
with a real MongoDB instance — catching behavior differences that
mongomock-motor might not surface (index behavior, aggregation pipeline
edge cases, BSON UUID handling).
"""

import os
import uuid
from pathlib import Path

import pytest
import pytest_asyncio

from slip_stream.adapters.persistence.db.generic_crud import VersionedMongoCRUD
from slip_stream.core.schema.registry import SchemaRegistry

SAMPLE_SCHEMAS_DIR = Path(__file__).parent.parent / "sample_schemas"

pytestmark = pytest.mark.skipif(
    os.environ.get("MONGO_URI") is None,
    reason="MONGO_URI not set",
)


@pytest.fixture
def registry():
    return SchemaRegistry(schema_dir=SAMPLE_SCHEMAS_DIR)


@pytest_asyncio.fixture
async def crud(real_db, registry):
    """Create a VersionedMongoCRUD for the widget schema."""
    doc_model, create_model, update_model = registry.get_model_for_version("widget")
    return VersionedMongoCRUD(
        db=real_db,
        collection_name="widget",
        model=doc_model,
        create_model=create_model,
        update_model=update_model,
    )


@pytest.fixture
def create_model(registry):
    _, create_model, _ = registry.get_model_for_version("widget")
    return create_model


class TestCreateIntegration:
    @pytest.mark.asyncio
    async def test_create_returns_document(self, crud, create_model):
        result = await crud.create(create_model(name="real-widget"))
        assert result.name == "real-widget"
        assert result.record_version == 1
        assert result.entity_id is not None

    @pytest.mark.asyncio
    async def test_create_generates_unique_entity_ids(self, crud, create_model):
        r1 = await crud.create(create_model(name="w1"))
        r2 = await crud.create(create_model(name="w2"))
        assert r1.entity_id != r2.entity_id

    @pytest.mark.asyncio
    async def test_uuid_roundtrip(self, crud, create_model):
        """UUIDs must survive write→read without corruption."""
        created = await crud.create(create_model(name="uuid-test"))
        fetched = await crud.get_by_entity_id(created.entity_id)
        assert fetched is not None
        assert fetched.entity_id == created.entity_id
        assert isinstance(fetched.entity_id, uuid.UUID)


class TestUpdateIntegration:
    @pytest.mark.asyncio
    async def test_update_increments_version(self, crud, create_model, registry):
        _, _, update_model = registry.get_model_for_version("widget")
        created = await crud.create(create_model(name="orig"))
        updated = await crud.update_by_entity_id(
            created.entity_id, update_model(color="red")
        )
        assert updated.record_version == 2
        assert updated.color == "red"
        assert updated.name == "orig"  # unchanged

    @pytest.mark.asyncio
    async def test_update_preserves_entity_id(self, crud, create_model, registry):
        _, _, update_model = registry.get_model_for_version("widget")
        created = await crud.create(create_model(name="orig"))
        updated = await crud.update_by_entity_id(
            created.entity_id, update_model(name="changed")
        )
        assert updated.entity_id == created.entity_id

    @pytest.mark.asyncio
    async def test_update_creates_new_document(self, crud, create_model, registry):
        """Update must create a NEW document (append-only), not mutate."""
        _, _, update_model = registry.get_model_for_version("widget")
        created = await crud.create(create_model(name="orig"))
        updated = await crud.update_by_entity_id(
            created.entity_id, update_model(name="v2")
        )
        # Different document IDs (new doc was inserted)
        assert updated.id != created.id


class TestDeleteIntegration:
    @pytest.mark.asyncio
    async def test_soft_delete(self, crud, create_model):
        created = await crud.create(create_model(name="to-delete"))
        deleted = await crud.delete_by_entity_id(created.entity_id)
        assert deleted.deleted_at is not None
        assert deleted.record_version == 2

    @pytest.mark.asyncio
    async def test_deleted_entity_not_in_list(self, crud, create_model):
        await crud.create(create_model(name="keep"))
        to_delete = await crud.create(create_model(name="remove"))
        await crud.delete_by_entity_id(to_delete.entity_id)

        active = await crud.list_latest_active()
        entity_ids = [doc.entity_id for doc in active]
        assert to_delete.entity_id not in entity_ids

    @pytest.mark.asyncio
    async def test_deleted_entity_returns_none_on_get(self, crud, create_model):
        created = await crud.create(create_model(name="gone"))
        await crud.delete_by_entity_id(created.entity_id)
        result = await crud.get_by_entity_id(created.entity_id)
        assert result is None


class TestListIntegration:
    @pytest.mark.asyncio
    async def test_list_returns_latest_versions_only(
        self, crud, create_model, registry
    ):
        """list_latest_active must return only the most recent version of each entity."""
        _, _, update_model = registry.get_model_for_version("widget")
        created = await crud.create(create_model(name="v1"))
        await crud.update_by_entity_id(created.entity_id, update_model(name="v2"))
        await crud.update_by_entity_id(created.entity_id, update_model(name="v3"))

        active = await crud.list_latest_active()
        matching = [d for d in active if d.entity_id == created.entity_id]
        assert len(matching) == 1
        assert matching[0].name == "v3"
        assert matching[0].record_version == 3

    @pytest.mark.asyncio
    async def test_count_active(self, crud, create_model):
        await crud.create(create_model(name="a"))
        await crud.create(create_model(name="b"))
        to_delete = await crud.create(create_model(name="c"))
        await crud.delete_by_entity_id(to_delete.entity_id)

        count = await crud.count_active()
        assert count == 2

    @pytest.mark.asyncio
    async def test_pagination(self, crud, create_model):
        """Skip/limit must work correctly with real MongoDB."""
        for i in range(5):
            await crud.create(create_model(name=f"item-{i}"))

        page = await crud.list_latest_active(skip=2, limit=2)
        assert len(page) == 2
