"""Tests for slip_stream/adapters/api/dependencies.py."""

import uuid

import pytest
from fastapi import HTTPException

from slip_stream.adapters.api.dependencies import (
    default_get_current_user,
    get_entity_uuid,
)


class TestGetEntityUuid:
    """Tests for get_entity_uuid() — UUID parsing and validation."""

    def test_get_entity_uuid_valid(self):
        """A well-formed UUID string is parsed and returned as a uuid.UUID."""
        valid_id = "12345678-1234-5678-1234-567812345678"
        result = get_entity_uuid(valid_id)

        assert isinstance(result, uuid.UUID)
        assert str(result) == valid_id

    def test_get_entity_uuid_invalid(self):
        """An invalid entity ID raises HTTPException with status 400."""
        with pytest.raises(HTTPException) as exc_info:
            get_entity_uuid("not-a-uuid")

        assert exc_info.value.status_code == 400
        assert "not-a-uuid" in exc_info.value.detail

    def test_get_entity_uuid_empty_string_raises(self):
        """An empty string raises HTTPException with status 400."""
        with pytest.raises(HTTPException) as exc_info:
            get_entity_uuid("")

        assert exc_info.value.status_code == 400

    def test_get_entity_uuid_partial_uuid_raises(self):
        """A partial UUID string raises HTTPException with status 400."""
        with pytest.raises(HTTPException) as exc_info:
            get_entity_uuid("12345678-1234-5678")

        assert exc_info.value.status_code == 400


class TestDefaultGetCurrentUser:
    """Tests for default_get_current_user() — header-based user resolution."""

    def test_default_get_current_user_returns_dict(self):
        """Calling with a user_id string returns a dict with 'id' key."""
        result = default_get_current_user(x_user_id="user-abc-123")

        assert isinstance(result, dict)
        assert result["id"] == "user-abc-123"

    def test_default_get_current_user_preserves_value(self):
        """The returned dict contains exactly the provided user ID, without transformation."""
        user_id = "some-opaque-token-42"
        result = default_get_current_user(x_user_id=user_id)

        assert result["id"] == user_id
