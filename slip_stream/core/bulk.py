"""Core bulk operation types for slip-stream.

Defines the result models used by bulk create, update, and delete
operations across all layers (ports, services, adapters, endpoints).
"""

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel


class BulkItemResult(BaseModel):
    """Result for a single item in a bulk operation."""

    index: int
    status: Literal["success", "error"]
    entity_id: Optional[str] = None
    record_version: Optional[int] = None
    error: Optional[str] = None
    error_code: Optional[int] = None


class BulkOperationResult(BaseModel):
    """Aggregate result for a bulk operation."""

    total: int
    succeeded: int
    failed: int
    items: List[BulkItemResult]
