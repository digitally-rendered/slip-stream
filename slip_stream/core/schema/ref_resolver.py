"""JSON Schema ``$ref`` resolver for local and internal references.

Resolves ``$ref`` pointers before model generation, producing a fully
dereferenced schema dict that the existing ``_json_type_to_python()``
can process unchanged.

Supported reference forms:

- **Internal**: ``{"$ref": "#/definitions/Address"}`` — resolved within the
  same schema document.
- **Local file**: ``{"$ref": "definitions/address.json"}`` — resolved relative
  to a configurable base path.
- **File + fragment**: ``{"$ref": "shared/common.json#/definitions/Status"}``
  — file is loaded, then the JSON Pointer fragment is applied.

Circular references are detected and raise ``ValueError``.
"""

from __future__ import annotations

import copy
import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class RefResolver:
    """Resolves JSON Schema ``$ref`` pointers.

    Args:
        base_path: Root directory for resolving relative file references.
            If ``None``, file references will raise ``ValueError``.
    """

    def __init__(self, base_path: Path | None = None) -> None:
        self._base_path = base_path
        self._file_cache: dict[str, dict[str, Any]] = {}

    def resolve(self, schema: dict[str, Any]) -> dict[str, Any]:
        """Return a fully dereferenced **copy** of *schema*.

        The original dict is never mutated.

        Raises:
            ValueError: On circular references or missing files.
        """
        schema_copy = copy.deepcopy(schema)
        return self._walk(schema_copy, root=schema_copy, seen=set())

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _walk(
        self,
        node: Any,
        root: dict[str, Any],
        seen: set[str],
    ) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                ref = node["$ref"]
                if ref in seen:
                    raise ValueError(f"Circular $ref detected: {ref}")
                seen = seen | {ref}
                resolved = self._resolve_ref(ref, root)
                # Recursively resolve any nested refs in the resolved content
                return self._walk(resolved, root, seen)

            return {k: self._walk(v, root, seen) for k, v in node.items()}

        if isinstance(node, list):
            return [self._walk(item, root, seen) for item in node]

        return node

    def _resolve_ref(self, ref: str, root: dict[str, Any]) -> dict[str, Any]:
        """Resolve a single ``$ref`` string."""
        if ref.startswith("#"):
            # Internal reference: #/definitions/Foo
            return self._resolve_pointer(ref[1:], root)

        if "#" in ref:
            # File + fragment: shared/common.json#/definitions/Status
            file_part, fragment = ref.split("#", 1)
            file_schema = self._load_file(file_part)
            return self._resolve_pointer(fragment, file_schema)

        # Plain file reference: definitions/address.json
        return self._load_file(ref)

    def _resolve_pointer(self, pointer: str, doc: dict[str, Any]) -> dict[str, Any]:
        """Resolve a JSON Pointer (e.g. ``/definitions/Address``) within *doc*."""
        if not pointer or pointer == "/":
            return copy.deepcopy(doc)

        parts = pointer.strip("/").split("/")
        current: Any = doc
        for part in parts:
            # Unescape JSON Pointer escapes
            part = part.replace("~1", "/").replace("~0", "~")
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                raise ValueError(
                    f"$ref pointer '{pointer}' could not be resolved: "
                    f"key '{part}' not found"
                )
        return copy.deepcopy(current)

    def _load_file(self, rel_path: str) -> dict[str, Any]:
        """Load and cache a JSON file relative to ``base_path``."""
        if self._base_path is None:
            raise ValueError(
                f"Cannot resolve file $ref '{rel_path}': no base_path configured"
            )

        if rel_path in self._file_cache:
            return copy.deepcopy(self._file_cache[rel_path])

        full_path = self._base_path / rel_path
        if not full_path.exists():
            raise ValueError(
                f"$ref file not found: {full_path} (resolved from '{rel_path}')"
            )

        try:
            with open(full_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON in $ref file {full_path}: {e}") from e

        self._file_cache[rel_path] = data
        return copy.deepcopy(data)
