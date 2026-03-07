"""Standalone hex-architecture checks for schemathesis fuzzing.

These checks are language-agnostic — they validate the common contract
that both slip-stream and stellar-drive implement:
- entity_id is a valid UUID
- record_version starts at 1 on create
- audit timestamps present
- no deleted_at on active documents
"""

import uuid

import schemathesis

_registered = False


def register_hex_checks():
    """Register all hex checks with schemathesis. Idempotent."""
    global _registered
    if _registered:
        return
    _registered = True

    @schemathesis.check
    def entity_id_is_valid_uuid(ctx, response, case):
        if case.method.upper() == "POST" and response.status_code == 201:
            body = _get_json(response)
            if body is None:
                return
            data = body.get("data", body)
            eid = data.get("entity_id")
            if eid is not None:
                try:
                    uuid.UUID(str(eid))
                except ValueError:
                    raise AssertionError(f"entity_id is not a valid UUID: {eid}")

    @schemathesis.check
    def record_version_starts_at_one(ctx, response, case):
        if case.method.upper() == "POST" and response.status_code == 201:
            body = _get_json(response)
            if body is None:
                return
            data = body.get("data", body)
            rv = data.get("record_version")
            if rv is not None and rv != 1:
                raise AssertionError(f"record_version on create should be 1, got {rv}")

    @schemathesis.check
    def audit_timestamps_present(ctx, response, case):
        if case.method.upper() == "POST" and response.status_code == 201:
            body = _get_json(response)
            if body is None:
                return
            data = body.get("data", body)
            for field in ("created_at", "updated_at"):
                if field not in data:
                    raise AssertionError(
                        f"Missing audit field '{field}' on create response"
                    )
        elif case.method.upper() == "PATCH" and response.status_code == 200:
            body = _get_json(response)
            if body is None:
                return
            data = body.get("data", body)
            if "updated_at" not in data:
                raise AssertionError("Missing 'updated_at' on update response")

    @schemathesis.check
    def no_deleted_at_on_active(ctx, response, case):
        if response.status_code in (200, 201):
            body = _get_json(response)
            if body is None:
                return
            data = body.get("data", body)
            if isinstance(data, dict) and data.get("deleted_at") is not None:
                raise AssertionError(
                    f"Active document has deleted_at set: {data['deleted_at']}"
                )


def _get_json(response):
    """Safely extract JSON from a response."""
    try:
        return response.json()
    except Exception:
        return None
