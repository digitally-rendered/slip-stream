"""Versioned MongoDB CRUD — append-only persistence with soft deletes."""

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Generic, List, Optional, Type, TypeVar

from bson.binary import Binary, UuidRepresentation
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel

from slip_stream.core.domain.base import BaseDocument

logger = logging.getLogger(__name__)

DocumentModelType = TypeVar("DocumentModelType", bound=BaseDocument)
CreateModelType = TypeVar("CreateModelType", bound=BaseModel)
UpdateModelType = TypeVar("UpdateModelType", bound=BaseModel)


class VersionedMongoCRUD(Generic[DocumentModelType, CreateModelType, UpdateModelType]):
    """Generic CRUD class for MongoDB documents with versioning.

    Implements an append-only versioning pattern where every write creates a
    new document version. No in-place mutations.

    Operations:
        - ``create()``: New document, ``record_version=1``, new ``entity_id``.
        - ``get_by_entity_id()``: Latest version by ``record_version`` desc, filters soft-deleted.
        - ``list_latest_active()``: Aggregation pipeline groups by ``entity_id``, takes latest.
        - ``update_by_entity_id()``: Copies current doc, increments ``record_version``, applies changes.
        - ``delete_by_entity_id()``: Creates tombstone with ``deleted_at`` set.
    """

    def __init__(
        self,
        db: AsyncIOMotorDatabase,
        collection_name: str,
        model: Type[DocumentModelType],
        create_model: Type[CreateModelType],
        update_model: Type[UpdateModelType],
    ):
        self.db = db
        self.collection_name = collection_name
        self.model = model
        self.create_model = create_model
        self.update_model = update_model

    def _is_mock_db(self) -> bool:
        """Check if we're using a mock database (e.g. mongomock-motor for tests)."""
        return any(
            "mongomock" in cls.__module__.lower() for cls in type(self.db).__mro__
        )

    def _uuid_to_binary(self, value: uuid.UUID) -> Binary:
        """Convert a UUID to BSON Binary for MongoDB storage."""
        return Binary(value.bytes, UuidRepresentation.PYTHON_LEGACY)

    def _prepare_for_insert(self, insert_dict: Dict[str, Any]) -> Dict[str, Any]:
        """Convert UUID fields to BSON Binary and handle mock DB quirks."""
        if isinstance(insert_dict.get("_id"), uuid.UUID):
            insert_dict["_id"] = self._uuid_to_binary(insert_dict["_id"])
        if isinstance(insert_dict.get("entity_id"), uuid.UUID):
            insert_dict["entity_id"] = self._uuid_to_binary(insert_dict["entity_id"])

        if self._is_mock_db():
            for key, val in insert_dict.items():
                if isinstance(val, uuid.UUID):
                    insert_dict[key] = str(val)

        return insert_dict

    async def create(
        self,
        data: CreateModelType,
        user_id: Optional[str] = None,
        entity_id: Optional[uuid.UUID] = None,
    ) -> DocumentModelType:
        """Creates a new document (first version) in the database."""
        now = datetime.now(timezone.utc)
        effective_entity_id = entity_id if entity_id else uuid.uuid4()

        document_data_dict = data.model_dump(exclude_unset=True)

        # Remove BaseDocument audit fields to prevent conflicts
        _audit_fields = {
            "id", "entity_id", "schema_version", "record_version",
            "created_at", "updated_at", "deleted_at",
            "created_by", "updated_by", "deleted_by",
        }
        for field_name in _audit_fields:
            document_data_dict.pop(field_name, None)

        document_to_insert = self.model(
            **document_data_dict,
            entity_id=effective_entity_id,
            created_at=now,
            updated_at=now,
            created_by=user_id,
            updated_by=user_id,
            record_version=1,
            schema_version=(
                self.model.model_fields["schema_version"].default
                if self.model.model_fields["schema_version"].default is not None
                else "1.0.0"
            ),
            deleted_at=None,
            deleted_by=None,
        )

        insert_dict = document_to_insert.model_dump(by_alias=True, exclude_none=False)
        insert_dict = self._prepare_for_insert(insert_dict)

        result = await self.db[self.collection_name].insert_one(insert_dict)
        logger.debug("Inserted into %s: _id=%s", self.collection_name, result.inserted_id)

        created_doc_from_db = await self.db[self.collection_name].find_one(
            {"_id": result.inserted_id}
        )

        if not created_doc_from_db:
            logger.error(
                "Failed to retrieve document after creation: collection=%s _id=%s",
                self.collection_name, result.inserted_id,
            )
            raise RuntimeError(
                f"Failed to retrieve document with _id {str(result.inserted_id)} "
                f"from collection {self.collection_name} after creation."
            )

        return self.model(**created_doc_from_db)

    async def get_by_entity_id(
        self, entity_id: uuid.UUID
    ) -> Optional[DocumentModelType]:
        """Retrieves the latest version of a document by its logical entity_id."""
        entity_id_binary = self._uuid_to_binary(entity_id)

        latest_doc = await self.db[self.collection_name].find_one(
            {"entity_id": entity_id_binary},
            sort=[("record_version", -1)],
        )

        if not latest_doc or latest_doc.get("deleted_at") is not None:
            return None

        return self.model(**latest_doc)

    async def list_latest_active(
        self,
        skip: int = 0,
        limit: int = 100,
        sort_by: str = "created_at",
        sort_order: int = -1,
        filter_criteria: Optional[Dict[str, Any]] = None,
    ) -> List[DocumentModelType]:
        """Lists the latest active versions of documents with pagination."""
        pipeline: List[Dict[str, Any]] = []

        if filter_criteria:
            processed_filter_criteria: Dict[str, Any] = {}
            for key, value in filter_criteria.items():
                if isinstance(value, uuid.UUID):
                    processed_filter_criteria[key] = self._uuid_to_binary(value)
                else:
                    processed_filter_criteria[key] = value
            pipeline.append({"$match": processed_filter_criteria})

        pipeline.append({"$sort": {"entity_id": 1, "record_version": -1}})

        pipeline.append(
            {
                "$group": {
                    "_id": "$entity_id",
                    "latest_version_doc": {"$first": "$$ROOT"},
                }
            }
        )

        pipeline.append({"$replaceRoot": {"newRoot": "$latest_version_doc"}})
        pipeline.append({"$match": {"deleted_at": None}})
        pipeline.append({"$sort": {sort_by: sort_order, "_id": -1}})
        pipeline.append({"$skip": skip})
        pipeline.append({"$limit": limit})

        cursor = self.db[self.collection_name].aggregate(pipeline)
        documents = []
        async for doc_from_db in cursor:
            documents.append(self.model(**doc_from_db))
        return documents

    async def update_by_entity_id(
        self,
        entity_id: uuid.UUID,
        data: UpdateModelType,
        user_id: Optional[str] = None,
    ) -> Optional[DocumentModelType]:
        """Updates an existing document by creating a new version."""
        now = datetime.now(timezone.utc)
        query_entity_id = self._uuid_to_binary(entity_id)

        latest_doc = await self.db[self.collection_name].find_one(
            {"entity_id": query_entity_id},
            sort=[("record_version", -1)],
        )

        if not latest_doc:
            return None

        if latest_doc.get("deleted_at") is not None:
            return None

        current_doc_from_db = latest_doc

        update_data_dict = data.model_dump(exclude_unset=True)
        has_changed = False
        for key, value in update_data_dict.items():
            if current_doc_from_db.get(key) != value:
                has_changed = True
                break

        if not has_changed:
            return None

        new_version_data_dict = current_doc_from_db.copy()
        del new_version_data_dict["_id"]

        protected_fields = {
            "id", "_id", "entity_id", "created_at", "created_by",
            "deleted_at", "deleted_by", "record_version", "schema_version",
            "updated_at", "updated_by",
        }
        for key, value in update_data_dict.items():
            if key not in protected_fields and value is not None:
                new_version_data_dict[key] = value

        new_version_data_dict["record_version"] = (
            current_doc_from_db["record_version"] + 1
        )
        new_version_data_dict["updated_at"] = now
        new_version_data_dict["updated_by"] = user_id
        new_version_data_dict["deleted_at"] = None
        new_version_data_dict["deleted_by"] = None

        new_version_model_instance = self.model(**new_version_data_dict)

        insert_dict = new_version_model_instance.model_dump(
            by_alias=True, exclude_none=False
        )
        insert_dict = self._prepare_for_insert(insert_dict)

        await self.db[self.collection_name].insert_one(insert_dict)

        return new_version_model_instance

    async def delete_by_entity_id(
        self, entity_id: uuid.UUID, user_id: Optional[str] = None
    ) -> Optional[DocumentModelType]:
        """Soft deletes an entity by creating a tombstone version."""
        now = datetime.now(timezone.utc)
        del_entity_id = self._uuid_to_binary(entity_id)

        latest_doc = await self.db[self.collection_name].find_one(
            {"entity_id": del_entity_id},
            sort=[("record_version", -1)],
        )

        if not latest_doc:
            return None

        if latest_doc.get("deleted_at") is not None:
            return None

        current_doc_from_db = latest_doc

        tombstone_data_dict = current_doc_from_db.copy()
        del tombstone_data_dict["_id"]

        tombstone_data_dict["record_version"] = (
            current_doc_from_db["record_version"] + 1
        )
        tombstone_data_dict["updated_at"] = now
        tombstone_data_dict["updated_by"] = user_id
        tombstone_data_dict["deleted_at"] = now
        tombstone_data_dict["deleted_by"] = user_id

        new_version_model_instance = self.model(**tombstone_data_dict)

        insert_dict = new_version_model_instance.model_dump(
            by_alias=True, exclude_none=False
        )
        insert_dict = self._prepare_for_insert(insert_dict)

        await self.db[self.collection_name].insert_one(insert_dict)

        return new_version_model_instance
