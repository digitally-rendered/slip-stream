"""Default FastAPI dependencies for slip-stream.

These are sensible defaults that consumers can override by passing their own
dependency callables to the EndpointFactory or SlipStream app builder.
"""

import uuid
from typing import Any, Dict

from fastapi import Header, HTTPException, status


def default_get_current_user(
    x_user_id: str = Header(...),
) -> Dict[str, Any]:
    """Default user dependency: reads user ID from X-User-ID header.

    Override this in production with your actual auth dependency.
    """
    return {"id": x_user_id}


def get_entity_uuid(entity_id: str) -> uuid.UUID:
    """Validate and convert an entity_id string to a UUID."""
    try:
        return uuid.UUID(entity_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid entity ID format: {entity_id}",
        ) from exc
