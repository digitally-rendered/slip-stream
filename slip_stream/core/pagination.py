"""Cursor-based pagination utilities for slip-stream.

Cursors are opaque base64-encoded JSON strings containing the sort field
values and document ID at the cursor boundary. This allows efficient
keyset pagination without requiring document counts or offset scans.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class PaginationMode(str, Enum):
    """Pagination strategy for a list request."""

    OFFSET = "offset"
    CURSOR = "cursor"


@dataclass
class CursorData:
    """Decoded cursor contents."""

    sort_values: Dict[str, Any]
    id: str


class PageInfo(BaseModel):
    """Relay-style page info for cursor pagination."""

    has_next_page: bool
    has_previous_page: bool
    start_cursor: Optional[str] = None
    end_cursor: Optional[str] = None


class CursorPage(BaseModel):
    """A page of results with cursor pagination metadata."""

    items: List[Any]
    page_info: PageInfo
    total_count: Optional[int] = None


def encode_cursor(sort_values: Dict[str, Any], doc_id: str) -> str:
    """Encode a cursor from sort field values and document ID."""
    payload = {"s": sort_values, "i": doc_id}
    return (
        base64.urlsafe_b64encode(json.dumps(payload, default=str).encode())
        .decode()
        .rstrip("=")
    )


def decode_cursor(cursor: str) -> CursorData:
    """Decode an opaque cursor string to its components."""
    padded = cursor + "=" * (4 - len(cursor) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode(padded))
    except (json.JSONDecodeError, Exception) as e:
        raise ValueError(f"Invalid cursor: {e}") from e
    return CursorData(
        sort_values=payload.get("s", {}),
        id=payload.get("i", ""),
    )


def detect_pagination_mode(
    after: Optional[str] = None,
    before: Optional[str] = None,
    first: Optional[int] = None,
    last: Optional[int] = None,
    skip: Optional[int] = None,
) -> PaginationMode:
    """Determine pagination mode from request parameters.

    Raises ValueError if both cursor and offset params are present.
    """
    has_cursor = any(p is not None for p in (after, before, first, last))
    has_offset = skip is not None and skip > 0

    if has_cursor and has_offset:
        raise ValueError(
            "Cannot use both cursor (after/before/first/last) and "
            "offset (skip) pagination in the same request"
        )

    return PaginationMode.CURSOR if has_cursor else PaginationMode.OFFSET
