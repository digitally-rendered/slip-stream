"""Custom schemathesis checks for hex-architecture invariants.

These checks validate the versioned document pattern that slip-stream
enforces:

1. ``entity_id`` is a valid UUID on POST 201 responses
2. ``record_version`` starts at 1 for new entities
3. ``created_at`` and ``updated_at`` are present on writes
4. Active documents (non-DELETE) never have ``deleted_at`` set

Register all checks with :func:`register_hex_checks` (called automatically
when importing this module inside a schemathesis test session).
"""

from __future__ import annotations

import uuid
from typing import Any

_checks_registered = False


def register_hex_checks() -> None:
    """Register all hex-architecture invariant checks with schemathesis.

    Safe to call multiple times — registration is idempotent.
    """
    global _checks_registered
    if _checks_registered:
        return

    try:
        import schemathesis
    except ImportError:
        raise ImportError(
            "schemathesis is required for hex-architecture checks. "
            "Install it with: pip install slip-stream[test]"
        ) from None

    @schemathesis.check
    def entity_id_is_valid_uuid(
        _ctx: Any, response: Any, case: Any
    ) -> bool | None:
        """POST 201 responses must contain a valid entity_id UUID."""
        if case.method.upper() != "POST" or response.status_code != 201:
            return None
        data = response.json()
        assert "entity_id" in data, "POST 201 response missing entity_id"
        try:
            uuid.UUID(str(data["entity_id"]))
        except ValueError as exc:
            raise AssertionError(
                f"entity_id is not a valid UUID: {data['entity_id']}"
            ) from exc
        return None

    @schemathesis.check
    def record_version_starts_at_one(
        _ctx: Any, response: Any, case: Any
    ) -> bool | None:
        """POST 201 responses must have record_version == 1."""
        if case.method.upper() != "POST" or response.status_code != 201:
            return None
        data = response.json()
        assert "record_version" in data, "POST 201 response missing record_version"
        assert (
            data["record_version"] == 1
        ), f"Expected record_version=1 for new entity, got {data['record_version']}"
        return None

    @schemathesis.check
    def audit_timestamps_present(
        _ctx: Any, response: Any, case: Any
    ) -> bool | None:
        """POST 201 and PATCH 200 responses must include created_at and updated_at."""
        method = case.method.upper()
        status = response.status_code
        if not (
            (method == "POST" and status == 201)
            or (method == "PATCH" and status == 200)
        ):
            return None
        data = response.json()
        assert "created_at" in data, f"{method} {status} response missing created_at"
        assert "updated_at" in data, f"{method} {status} response missing updated_at"
        return None

    @schemathesis.check
    def no_deleted_at_on_active_documents(
        _ctx: Any, response: Any, case: Any
    ) -> bool | None:
        """Active documents (POST/PATCH/GET 2xx) must not have deleted_at set."""
        method = case.method.upper()
        if method not in ("POST", "PATCH", "GET"):
            return None
        if response.status_code < 200 or response.status_code >= 300:
            return None
        try:
            data = response.json()
        except Exception:
            return None
        docs = data if isinstance(data, list) else [data]
        for doc in docs:
            if not isinstance(doc, dict):
                continue
            if "deleted_at" in doc:
                assert (
                    doc["deleted_at"] is None
                ), f"Active document has deleted_at={doc['deleted_at']}"
        return None

    _checks_registered = True
