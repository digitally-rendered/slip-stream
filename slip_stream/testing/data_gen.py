"""Test data generators for slip-stream entities.

Generates valid test data from resolved Pydantic models, used by the
stateful lifecycle tests and the CLI test runner.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any, Dict, Union, get_args, get_origin

from pydantic import BaseModel

_AUDIT_FIELDS = frozenset({
    "id", "entity_id", "schema_version", "record_version",
    "created_at", "updated_at", "deleted_at",
    "created_by", "updated_by", "deleted_by",
})


def _generate_value_for_type(annotation: Any, field_name: str) -> Any:
    """Generate a test value based on a Python type annotation."""
    origin = get_origin(annotation)
    args = get_args(annotation)

    if origin is Union:
        non_none_args = [a for a in args if a is not type(None)]
        if non_none_args:
            return _generate_value_for_type(non_none_args[0], field_name)
        return None

    if origin is dict or annotation is dict:
        return {}
    if origin is list or annotation is list:
        return []

    if isinstance(annotation, type) and issubclass(annotation, enum.Enum):
        members = list(annotation)
        return members[0].value if members else f"test-{field_name}"

    if annotation is str:
        return f"test-{field_name}"
    if annotation is int:
        return 42
    if annotation is float:
        return 42.0
    if annotation is bool:
        return True
    if annotation is uuid.UUID:
        return str(uuid.uuid4())
    if annotation is datetime:
        return "2023-01-01T12:00:00Z"
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return _generate_from_pydantic_model(annotation)
    return f"test-{field_name}"


def _generate_from_pydantic_model(
    model_cls: type[BaseModel], skip_audit: bool = False
) -> Dict[str, Any]:
    """Generate test data from a Pydantic model class."""
    data: Dict[str, Any] = {}
    for field_name, field_info in model_cls.model_fields.items():
        if skip_audit and field_name in _AUDIT_FIELDS:
            continue
        if field_info.is_required():
            data[field_name] = _generate_value_for_type(
                field_info.annotation, field_name
            )
    return data


def generate_create_data(
    schema_name: str,
    container: Any = None,
) -> Dict[str, Any]:
    """Generate valid create data from the resolved Create model.

    Args:
        schema_name: Name of the schema entity.
        container: Optional ``EntityContainer`` instance. If not provided,
            uses ``get_container()``.

    Returns:
        A dict of field values suitable for a POST request body.
    """
    if container is None:
        from slip_stream.container import get_container
        container = get_container()
    registration = container.get(schema_name)
    return _generate_from_pydantic_model(registration.create_model)


def generate_update_payload(
    schema_name: str,
    created_data: dict,
    container: Any = None,
) -> dict:
    """Build a minimal update payload that changes at least one field.

    Args:
        schema_name: Name of the schema entity.
        created_data: The response from the create endpoint.
        container: Optional ``EntityContainer`` instance.

    Returns:
        A dict with at least one changed field, or empty dict if no
        updatable fields exist.
    """
    if container is None:
        from slip_stream.container import get_container
        container = get_container()
    registration = container.get(schema_name)
    update_model_cls = registration.update_model

    def _changed_value(annotation: Any, field_name: str) -> Any:
        origin = get_origin(annotation)
        args = get_args(annotation)

        if origin is Union:
            non_none_args = [a for a in args if a is not type(None)]
            if non_none_args:
                return _changed_value(non_none_args[0], field_name)
            return None

        if origin is dict or annotation is dict:
            return {"updated_key": "updated_value"}
        if origin is list or annotation is list:
            return None

        if isinstance(annotation, type) and issubclass(annotation, enum.Enum):
            members = list(annotation)
            return members[1].value if len(members) > 1 else members[0].value

        if annotation is str:
            return "updated-value"
        if annotation is int:
            return 99
        if annotation is float:
            return 99.9
        if annotation is bool:
            return False
        if annotation is uuid.UUID:
            return None
        if annotation is datetime:
            return "2025-06-15T12:00:00Z"
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            return None
        return None

    for field_name, field_info in update_model_cls.model_fields.items():
        if field_name in _AUDIT_FIELDS:
            continue
        val = _changed_value(field_info.annotation, field_name)
        if val is not None:
            return {field_name: val}

    update_field_names = set(update_model_cls.model_fields.keys())
    for key, val in created_data.items():
        if key in _AUDIT_FIELDS or key not in update_field_names:
            continue
        if isinstance(val, str):
            return {key: f"{val} updated"}

    return {}
