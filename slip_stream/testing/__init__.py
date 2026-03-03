"""Reusable testing utilities for slip-stream applications.

Provides schemathesis-based property testing, custom hex-architecture
invariant checks, and a test runner for CLI integration.

Optional dependency: ``pip install slip-stream[test]``
"""

from slip_stream.testing.app_builder import build_test_app
from slip_stream.testing.checks import register_hex_checks
from slip_stream.testing.data_gen import generate_create_data, generate_update_payload
from slip_stream.testing.openapi import downgrade_openapi
from slip_stream.testing.runner import SchemaTestRunner

__all__ = [
    "build_test_app",
    "register_hex_checks",
    "generate_create_data",
    "generate_update_payload",
    "downgrade_openapi",
    "SchemaTestRunner",
]
