"""Base document model with versioned audit fields for all slip-stream entities."""

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from bson.binary import Binary, UuidRepresentation
from pydantic import BaseModel, Field, field_serializer, model_validator


class BaseDocument(BaseModel):
    """Base model for all MongoDB documents, providing common audit fields.

    All persisted entities extend this class. The versioned document pattern
    means every write creates a new document version — no in-place mutations.

    Fields:
        id: Unique per document version (each version gets a new id).
        entity_id: Stable across all versions of a logical entity.
        record_version: Increments with each new version (1, 2, 3, ...).
        schema_version: Tracks schema evolution.
        created_at/updated_at: Timestamps.
        deleted_at: Set to non-None for soft-delete (tombstone).
        created_by/updated_by/deleted_by: User audit trail.
    """

    id: uuid.UUID = Field(default_factory=uuid.uuid4, alias="_id")
    entity_id: uuid.UUID
    schema_version: str = Field(default="1.0.0")
    record_version: int = Field(default=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    deleted_at: Optional[datetime] = None
    created_by: Optional[str] = None
    updated_by: Optional[str] = None
    deleted_by: Optional[str] = None

    model_config = {
        "populate_by_name": True,
        "from_attributes": True,
    }

    @field_serializer("created_at", "updated_at", "deleted_at", when_used="json")
    def serialize_datetime(self, value: Optional[datetime]) -> Optional[str]:
        """Serialize datetime fields to ISO 8601 format with 'Z' suffix."""
        if value is None:
            return None
        return value.isoformat().replace("+00:00", "Z")

    @model_validator(mode="before")
    @classmethod
    def normalize_uuids(cls, data: Any) -> Any:
        """Convert various UUID representations (Binary, str) to uuid.UUID objects.

        Handles BSON Binary UUIDs from MongoDB and string UUIDs from JSON.
        Automatically detects UUID fields by name pattern: ``_id``, ``entity_id``,
        and any field ending in ``_id``. Fields ending in ``_by`` are kept as strings.
        """
        if isinstance(data, dict):
            # Fields that should remain as strings even if they contain UUID values
            string_uuid_fields = {"created_by", "updated_by", "deleted_by"}

            for key, value in data.items():
                # Handle Binary UUIDs from MongoDB
                if (
                    isinstance(value, Binary)
                    and value.subtype == UuidRepresentation.PYTHON_LEGACY
                ):
                    data[key] = uuid.UUID(bytes=value)
                # Handle string UUIDs for fields that should be UUID objects
                elif isinstance(value, str) and cls._is_uuid_field(key) and key not in string_uuid_fields:
                    try:
                        data[key] = uuid.UUID(value)
                    except ValueError:
                        pass
                # Handle UUID objects for fields that should be strings
                elif isinstance(value, uuid.UUID) and key in string_uuid_fields:
                    data[key] = str(value)
                # Handle lists
                elif isinstance(value, list):
                    if key.endswith("_ids"):
                        data[key] = [cls._convert_to_uuid(item) for item in value]
                    else:
                        data[key] = [
                            (
                                uuid.UUID(bytes=item)
                                if isinstance(item, Binary)
                                and item.subtype == UuidRepresentation.PYTHON_LEGACY
                                else item
                            )
                            for item in value
                        ]
        return data

    @classmethod
    def _is_uuid_field(cls, field_name: str) -> bool:
        """Determine if a field should be treated as a UUID field by name pattern."""
        return field_name in ("id", "_id", "entity_id") or (
            field_name.endswith("_id") and not field_name.endswith("_by")
        )

    @classmethod
    def _convert_to_uuid(cls, value: Any) -> Any:
        """Helper method to convert a value to UUID if possible."""
        if (
            isinstance(value, Binary)
            and value.subtype == UuidRepresentation.PYTHON_LEGACY
        ):
            return uuid.UUID(bytes=value)
        if isinstance(value, str):
            try:
                return uuid.UUID(value)
            except ValueError:
                return value
        return value
