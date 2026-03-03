"""Start a slip-stream test app for OWASP ZAP scanning.

Usage:
    python tests/security/zap_target.py

Starts a FastAPI app on port 8099 with sample schemas loaded and
mongomock-motor as the database backend. The OpenAPI spec is available
at http://localhost:8099/openapi.json for ZAP API scan consumption.
"""

from __future__ import annotations

import sys
from pathlib import Path

import uvicorn

# Ensure the project root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from slip_stream.testing.app_builder import build_test_app  # noqa: E402

app = build_test_app(
    schema_dir=Path(__file__).resolve().parents[1] / "sample_schemas",
    api_prefix="/api/v1",
    title="slip-stream ZAP Target",
)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8099, log_level="warning")
