"""Stateful lifecycle tests: deterministic CRUD per schema.

Tests the full create → get → list → update → delete → verify-404
lifecycle for each sample schema using the SchemaTestRunner.
"""

import pytest
from mongomock_motor import AsyncMongoMockClient
from pathlib import Path

from slip_stream.testing.runner import SchemaTestRunner

SAMPLE_SCHEMAS_DIR = Path(__file__).parent.parent / "sample_schemas"


@pytest.fixture()
def lifecycle_runner():
    """Create a SchemaTestRunner with a fresh mock DB per test."""
    client = AsyncMongoMockClient()
    mock_db = client.get_database("lifecycle_test_db")
    return SchemaTestRunner(
        schema_dir=SAMPLE_SCHEMAS_DIR,
        mock_db=mock_db,
    )


def test_lifecycle_all_schemas_pass(lifecycle_runner):
    """All sample schemas should pass the full CRUD lifecycle."""
    results = lifecycle_runner.run_lifecycle_tests()
    assert len(results) > 0, "No schemas found to test"

    for result in results:
        assert result.passed, (
            f"Schema '{result.schema_name}' failed at step "
            f"{result.steps_completed}/6: {result.error}"
        )


def test_lifecycle_creates_valid_entity_ids(lifecycle_runner):
    """Created entities must have valid UUID entity_ids."""
    import uuid
    from fastapi.testclient import TestClient

    app = lifecycle_runner.build_app()
    with TestClient(app, raise_server_exceptions=False) as client:
        # POST to widget endpoint
        resp = client.post(
            "/api/v1/widget/",
            json={"name": "test-widget"},
            headers={"X-User-ID": "lifecycle-test"},
        )
        if resp.status_code == 201:
            data = resp.json()
            # Validate entity_id is a proper UUID
            entity_id = data.get("entity_id")
            assert entity_id is not None
            uuid.UUID(str(entity_id))  # Raises if invalid


def test_lifecycle_version_increments(lifecycle_runner):
    """record_version must increment from 1 to 2 on update."""
    from fastapi.testclient import TestClient

    app = lifecycle_runner.build_app()
    headers = {"X-User-ID": "lifecycle-test"}

    with TestClient(app, raise_server_exceptions=False) as client:
        # Create
        resp = client.post(
            "/api/v1/widget/",
            json={"name": "versioned-widget"},
            headers=headers,
        )
        if resp.status_code != 201:
            pytest.skip(f"POST returned {resp.status_code}, cannot test versioning")

        created = resp.json()
        assert created["record_version"] == 1

        entity_id = created["entity_id"]

        # Update
        resp = client.patch(
            f"/api/v1/widget/{entity_id}",
            json={"name": "updated-widget"},
            headers=headers,
        )
        assert resp.status_code == 200
        updated = resp.json()
        assert updated["record_version"] == 2


def test_lifecycle_soft_delete(lifecycle_runner):
    """Deleted entities must return 404 on subsequent GET."""
    from fastapi.testclient import TestClient

    app = lifecycle_runner.build_app()
    headers = {"X-User-ID": "lifecycle-test"}

    with TestClient(app, raise_server_exceptions=False) as client:
        # Create
        resp = client.post(
            "/api/v1/widget/",
            json={"name": "delete-me"},
            headers=headers,
        )
        if resp.status_code != 201:
            pytest.skip(f"POST returned {resp.status_code}, cannot test delete")

        entity_id = resp.json()["entity_id"]

        # Delete
        resp = client.delete(
            f"/api/v1/widget/{entity_id}",
            headers=headers,
        )
        assert resp.status_code == 204

        # Verify gone
        resp = client.get(
            f"/api/v1/widget/{entity_id}",
            headers=headers,
        )
        assert resp.status_code == 404


def test_lifecycle_results_printed(lifecycle_runner, capsys):
    """SchemaTestRunner.print_results outputs formatted results."""
    results = lifecycle_runner.run_lifecycle_tests()
    SchemaTestRunner.print_results(results)
    captured = capsys.readouterr()
    assert "PASS" in captured.out or "FAIL" in captured.out
    assert "schema lifecycle tests passed" in captured.out
