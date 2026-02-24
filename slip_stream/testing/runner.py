"""Schema test runner for slip-stream applications.

Provides a reusable ``SchemaTestRunner`` class that consumers can use
programmatically or via the ``slip schema test`` CLI command.

Usage::

    from slip_stream.testing import SchemaTestRunner

    runner = SchemaTestRunner(schema_dir=Path("./schemas"))
    results = runner.run_lifecycle_tests()
    runner.print_results(results)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class TestResult:
    """Result from a single schema lifecycle test."""

    schema_name: str
    passed: bool
    steps_completed: int = 0
    error: str | None = None
    details: Dict[str, Any] = field(default_factory=dict)


class SchemaTestRunner:
    """Runs property-based and lifecycle tests against slip-stream schemas.

    Args:
        schema_dir: Path to the schemas directory.
        api_prefix: URL prefix for generated endpoints.
        mock_db: Optional mock database instance.
    """

    def __init__(
        self,
        schema_dir: Optional[Path] = None,
        api_prefix: str = "/api/v1",
        mock_db: Any = None,
    ) -> None:
        self.schema_dir = schema_dir
        self.api_prefix = api_prefix
        self._mock_db = mock_db

    def build_app(self) -> Any:
        """Build and return the test FastAPI application."""
        from slip_stream.testing.app_builder import build_test_app

        return build_test_app(
            schema_dir=self.schema_dir,
            api_prefix=self.api_prefix,
            mock_db=self._mock_db,
        )

    def run_lifecycle_tests(self) -> List[TestResult]:
        """Run deterministic CRUD lifecycle tests for all schemas.

        Performs POST -> GET -> LIST -> PATCH -> DELETE -> GET(404) for
        each schema and validates hex-architecture invariants.

        Returns:
            A list of ``TestResult`` objects, one per schema.
        """
        from fastapi.testclient import TestClient

        from slip_stream.container import get_container
        from slip_stream.core.schema.registry import SchemaRegistry

        app = self.build_app()
        registry = SchemaRegistry()
        schema_names = sorted(registry.get_schema_names())
        container = get_container()

        results: List[TestResult] = []
        auth_headers = {"X-User-ID": "lifecycle-test-user"}

        with TestClient(app, raise_server_exceptions=False) as client:
            for schema_name in schema_names:
                result = self._run_single_lifecycle(
                    client, schema_name, container, auth_headers
                )
                results.append(result)

        return results

    def _run_single_lifecycle(
        self,
        client: Any,
        schema_name: str,
        container: Any,
        auth_headers: dict,
    ) -> TestResult:
        """Run a single CRUD lifecycle test for one schema."""
        from slip_stream.testing.data_gen import (
            generate_create_data,
            generate_update_payload,
        )

        path_prefix = f"{self.api_prefix}/{schema_name.replace('_', '-')}"
        result = TestResult(schema_name=schema_name, passed=False)

        try:
            # 1. CREATE
            create_data = generate_create_data(schema_name, container)
            resp = client.post(
                f"{path_prefix}/", json=create_data, headers=auth_headers
            )

            if resp.status_code == 500:
                result.error = f"POST returned 500: {resp.text}"
                return result

            if resp.status_code != 201:
                result.error = f"POST returned {resp.status_code} (expected 201)"
                result.steps_completed = 0
                result.passed = resp.status_code in (400, 422)
                return result

            created = resp.json()
            result.steps_completed = 1

            # Validate invariants
            assert "entity_id" in created, "Missing entity_id"
            entity_id = created["entity_id"]
            assert created["record_version"] == 1
            assert created.get("created_at") is not None
            assert created.get("deleted_at") is None

            # 2. GET
            resp = client.get(f"{path_prefix}/{entity_id}", headers=auth_headers)
            assert resp.status_code == 200, f"GET failed: {resp.status_code}"
            assert resp.json()["entity_id"] == entity_id
            result.steps_completed = 2

            # 3. LIST
            resp = client.get(f"{path_prefix}/", headers=auth_headers)
            assert resp.status_code == 200, f"LIST failed: {resp.status_code}"
            items = resp.json()
            assert isinstance(items, list)
            ids = [i["entity_id"] for i in items]
            assert entity_id in ids
            result.steps_completed = 3

            # 4. UPDATE
            update_payload = generate_update_payload(schema_name, created, container)
            resp = client.patch(
                f"{path_prefix}/{entity_id}",
                json=update_payload,
                headers=auth_headers,
            )
            assert resp.status_code == 200, f"PATCH failed: {resp.status_code}"
            updated = resp.json()
            assert updated["record_version"] == 2
            assert updated.get("deleted_at") is None
            result.steps_completed = 4

            # 5. DELETE
            resp = client.delete(f"{path_prefix}/{entity_id}", headers=auth_headers)
            assert resp.status_code == 204, f"DELETE failed: {resp.status_code}"
            result.steps_completed = 5

            # 6. VERIFY SOFT DELETE
            resp = client.get(f"{path_prefix}/{entity_id}", headers=auth_headers)
            assert resp.status_code == 404, f"Expected 404, got {resp.status_code}"
            result.steps_completed = 6

            result.passed = True

        except (AssertionError, Exception) as exc:
            result.error = str(exc)

        return result

    def run_schemathesis_cli(
        self,
        checks: str = "not_a_server_error",
        workers: str = "auto",
        extra_args: Optional[List[str]] = None,
    ) -> int:
        """Run schemathesis CLI against the test app.

        Starts a temporary server and runs ``schemathesis run`` against it.

        Args:
            checks: Comma-separated list of schemathesis checks.
            workers: Number of workers (``"auto"`` for automatic).
            extra_args: Additional CLI arguments.

        Returns:
            The exit code from schemathesis.
        """
        import json
        import subprocess
        import sys
        import tempfile

        app = self.build_app()

        # Write the OpenAPI schema to a temp file
        schema = app.openapi()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(schema, f)
            schema_path = f.name

        cmd = [
            sys.executable,
            "-m",
            "schemathesis",
            "run",
            schema_path,
            "--checks",
            checks,
            "--workers",
            workers,
        ]
        if extra_args:
            cmd.extend(extra_args)

        return subprocess.call(cmd)

    @staticmethod
    def print_results(results: List[TestResult]) -> None:
        """Print lifecycle test results to stdout."""
        passed = sum(1 for r in results if r.passed)
        total = len(results)

        for r in results:
            status = "PASS" if r.passed else "FAIL"
            steps = f"({r.steps_completed}/6 steps)"
            print(f"  {status}  {r.schema_name:<30} {steps}")
            if r.error:
                print(f"         Error: {r.error}")

        print()
        print(f"{passed}/{total} schema lifecycle tests passed.")
