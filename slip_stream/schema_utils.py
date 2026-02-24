"""Shared schema utilities used by CLI and MCP server.

Functions in this module are intentionally side-effect-free (except file I/O)
so that they can be safely called from both the interactive CLI and the MCP
server's tool handlers.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List


# ---------------------------------------------------------------------------
# Schema template — the canonical JSON Schema skeleton for new entities
# ---------------------------------------------------------------------------

SCHEMA_TEMPLATE = """\
{{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "{title}",
  "description": "{description}",
  "version": "1.0.0",
  "type": "object",
  "required": ["name"],
  "properties": {{
    "id": {{ "type": "string", "format": "uuid" }},
    "entity_id": {{ "type": "string", "format": "uuid" }},
    "schema_version": {{ "type": "string", "default": "1.0.0" }},
    "record_version": {{ "type": "integer", "default": 1 }},
    "created_at": {{ "type": "string", "format": "date-time" }},
    "updated_at": {{ "type": "string", "format": "date-time" }},
    "deleted_at": {{ "type": "string", "format": "date-time" }},
    "created_by": {{ "type": "string" }},
    "updated_by": {{ "type": "string" }},
    "deleted_by": {{ "type": "string" }},
    "name": {{
      "type": "string",
      "description": "Name of the {title_lower}"
    }}
  }}
}}
"""


# ---------------------------------------------------------------------------
# Name helpers
# ---------------------------------------------------------------------------

def snake_case(name: str) -> str:
    """Normalise a name to snake_case for file/schema naming."""
    s = re.sub(r"[^a-zA-Z0-9]", "_", name)
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", s)
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s)
    return s.lower().strip("_")


def title_case(snake: str) -> str:
    """Convert a snake_case string to TitleCase."""
    return "".join(word.capitalize() for word in snake.split("_"))


# ---------------------------------------------------------------------------
# Schema file operations
# ---------------------------------------------------------------------------

def create_schema_file(
    schemas_dir: Path,
    name: str,
    description: str | None = None,
) -> Path:
    """Create a new JSON schema file in *schemas_dir*.

    Args:
        schemas_dir: Directory containing schema ``*.json`` files.
        name: Entity name (will be normalized to snake_case).
        description: Optional schema description.

    Returns:
        Path to the created file.

    Raises:
        FileExistsError: If the schema file already exists.
    """
    snake = snake_case(name)
    title = title_case(snake)
    target = schemas_dir / f"{snake}.json"

    if target.exists():
        raise FileExistsError(f"Schema already exists: {target}")

    desc = description or f"A {title} entity."
    target.write_text(
        SCHEMA_TEMPLATE.format(
            title=title,
            description=desc,
            title_lower=title.lower(),
        )
    )
    return target


def validate_schema_file(path: Path) -> List[str]:
    """Validate a single schema file.

    Returns:
        A list of issue strings (empty if the schema is valid).
    """
    issues: List[str] = []
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        return [f"invalid JSON: {e}"]

    if "title" not in data:
        issues.append("missing 'title'")
    if "version" not in data:
        issues.append("missing 'version'")
    if data.get("type") != "object":
        issues.append("'type' must be 'object'")
    if "properties" not in data:
        issues.append("missing 'properties'")
    return issues


def validate_all_schemas(schemas_dir: Path) -> Dict[str, List[str]]:
    """Validate all JSON schemas in a directory.

    Returns:
        Mapping of ``{relative_filename: [issues]}`` for every ``*.json``
        file found.  Files with no issues have an empty list.
    """
    results: Dict[str, List[str]] = {}
    for f in sorted(schemas_dir.glob("**/*.json")):
        issues = validate_schema_file(f)
        results[str(f.relative_to(schemas_dir))] = issues
    return results
