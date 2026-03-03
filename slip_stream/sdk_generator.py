"""SDK/client code generator for slip-stream APIs.

Reads JSON Schema definitions and generates typed Python client classes
with async CRUD methods.  The generated client uses ``httpx`` for HTTP
and includes Pydantic request/response models derived from the schema.

Usage::

    from slip_stream.sdk_generator import generate_sdk

    code = generate_sdk(
        schemas={"widget": schema_dict, "gadget": schema_dict},
        base_url="http://localhost:8000/api/v1",
    )
    Path("client.py").write_text(code)

The generated module is self-contained — it only depends on ``httpx``
and ``pydantic``.
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# JSON Schema → Python type mapping
# ---------------------------------------------------------------------------

_TYPE_MAP = {
    "string": "str",
    "integer": "int",
    "number": "float",
    "boolean": "bool",
    "array": "list",
    "object": "dict",
}

_AUDIT_FIELDS = frozenset(
    {
        "id",
        "_id",
        "entity_id",
        "schema_version",
        "record_version",
        "created_at",
        "updated_at",
        "deleted_at",
        "created_by",
        "updated_by",
        "deleted_by",
    }
)


def _python_type(prop: dict[str, Any]) -> str:
    """Map a JSON Schema property to a Python type annotation."""
    json_type = prop.get("type", "Any")

    if json_type == "array":
        items = prop.get("items", {})
        inner = _TYPE_MAP.get(items.get("type", "string"), "Any")
        return f"list[{inner}]"

    return _TYPE_MAP.get(json_type, "Any")


def _snake_to_pascal(name: str) -> str:
    """Convert a snake_case name to PascalCase."""
    return "".join(word.capitalize() for word in name.split("_"))


# ---------------------------------------------------------------------------
# Code generation
# ---------------------------------------------------------------------------


def generate_sdk(
    schemas: dict[str, dict[str, Any]],
    base_url: str = "http://localhost:8000/api/v1",
    module_docstring: str | None = None,
) -> str:
    """Generate a typed Python SDK client module from JSON Schema definitions.

    Args:
        schemas: Mapping of schema name → JSON Schema dict.
        base_url: The API base URL to embed as default.
        module_docstring: Optional module-level docstring.

    Returns:
        A string containing the complete Python module source code.
    """
    parts: list[str] = []

    # Module header
    doc = module_docstring or "Auto-generated API client for slip-stream."
    parts.append(f'"""{doc}"""')
    parts.append("")
    parts.append("from __future__ import annotations")
    parts.append("")
    parts.append("from datetime import datetime")
    parts.append("from typing import Any, Optional")
    parts.append("from uuid import UUID")
    parts.append("")
    parts.append("import httpx")
    parts.append("from pydantic import BaseModel, Field")
    parts.append("")
    parts.append("")

    # Generate models and client methods per schema
    client_methods: list[str] = []

    for schema_name, schema_dict in sorted(schemas.items()):
        pascal = _snake_to_pascal(schema_name)
        properties = schema_dict.get("properties", {})
        required = set(schema_dict.get("required", []))

        # --- Document model (full response) ---
        parts.append(f"class {pascal}(BaseModel):")
        doc_desc = schema_dict.get("description", f"A {pascal} entity.")
        parts.append(f'    """{doc_desc}"""')
        parts.append("")
        # Audit fields
        parts.append("    id: Optional[UUID] = None")
        parts.append("    entity_id: Optional[UUID] = None")
        parts.append('    schema_version: str = "1.0.0"')
        parts.append("    record_version: int = 1")
        parts.append("    created_at: Optional[datetime] = None")
        parts.append("    updated_at: Optional[datetime] = None")
        parts.append("    deleted_at: Optional[datetime] = None")
        parts.append("    created_by: Optional[str] = None")
        parts.append("    updated_by: Optional[str] = None")
        parts.append("    deleted_by: Optional[str] = None")

        # User-defined properties
        user_props = {k: v for k, v in properties.items() if k not in _AUDIT_FIELDS}
        for prop_name, prop_def in sorted(user_props.items()):
            py_type = _python_type(prop_def)
            desc = prop_def.get("description", "")
            default = prop_def.get("default")

            if prop_name in required and default is None:
                field_def = f"    {prop_name}: {py_type}"
            else:
                if default is not None:
                    default_repr = repr(default)
                    field_def = f"    {prop_name}: Optional[{py_type}] = {default_repr}"
                else:
                    field_def = f"    {prop_name}: Optional[{py_type}] = None"

            if desc:
                field_def += f"  # {desc}"
            parts.append(field_def)

        parts.append("")
        parts.append("")

        # --- Create model ---
        parts.append(f"class {pascal}Create(BaseModel):")
        parts.append(f'    """Create payload for {pascal}."""')
        parts.append("")
        for prop_name, prop_def in sorted(user_props.items()):
            py_type = _python_type(prop_def)
            default = prop_def.get("default")
            if prop_name in required and default is None:
                parts.append(f"    {prop_name}: {py_type}")
            else:
                if default is not None:
                    parts.append(
                        f"    {prop_name}: Optional[{py_type}] = {repr(default)}"
                    )
                else:
                    parts.append(f"    {prop_name}: Optional[{py_type}] = None")
        if not user_props:
            parts.append("    pass")
        parts.append("")
        parts.append("")

        # --- Update model ---
        parts.append(f"class {pascal}Update(BaseModel):")
        parts.append(f'    """Update payload for {pascal}."""')
        parts.append("")
        for prop_name, prop_def in sorted(user_props.items()):
            py_type = _python_type(prop_def)
            parts.append(f"    {prop_name}: Optional[{py_type}] = None")
        if not user_props:
            parts.append("    pass")
        parts.append("")
        parts.append("")

        # --- Client methods for this schema ---
        client_methods.append(_generate_client_methods(schema_name, pascal))

    # --- Client class ---
    parts.append("class SlipStreamClient:")
    parts.append('    """Typed async HTTP client for the slip-stream API."""')
    parts.append("")
    parts.append("    def __init__(")
    parts.append(f'        self, base_url: str = "{base_url}",')
    parts.append("        headers: dict[str, str] | None = None,")
    parts.append("        timeout: float = 30.0,")
    parts.append("    ) -> None:")
    parts.append("        self._base_url = base_url.rstrip('/')")
    parts.append("        self._client = httpx.AsyncClient(")
    parts.append("            base_url=self._base_url,")
    parts.append("            headers=headers or {},")
    parts.append("            timeout=timeout,")
    parts.append("        )")
    parts.append("")
    parts.append("    async def close(self) -> None:")
    parts.append('        """Close the underlying HTTP client."""')
    parts.append("        await self._client.aclose()")
    parts.append("")
    parts.append("    async def __aenter__(self) -> 'SlipStreamClient':")
    parts.append("        return self")
    parts.append("")
    parts.append("    async def __aexit__(self, *args: Any) -> None:")
    parts.append("        await self.close()")
    parts.append("")

    for method_block in client_methods:
        parts.append(method_block)

    return "\n".join(parts) + "\n"


def _generate_client_methods(schema_name: str, pascal: str) -> str:
    """Generate CRUD client methods for a single schema."""
    lines = []

    # create
    lines.append(f"    async def create_{schema_name}(")
    lines.append(f"        self, data: {pascal}Create")
    lines.append(f"    ) -> {pascal}:")
    lines.append(f'        """Create a new {pascal}."""')
    lines.append(
        f'        resp = await self._client.post("/{schema_name}/", json=data.model_dump(exclude_unset=True))'
    )
    lines.append(f"        resp.raise_for_status()")
    lines.append(f"        return {pascal}.model_validate(resp.json())")
    lines.append("")

    # get
    lines.append(f"    async def get_{schema_name}(")
    lines.append(f"        self, entity_id: str | UUID")
    lines.append(f"    ) -> {pascal}:")
    lines.append(f'        """Get a {pascal} by entity_id."""')
    lines.append(
        f'        resp = await self._client.get(f"/{schema_name}/{{entity_id}}")'
    )
    lines.append(f"        resp.raise_for_status()")
    lines.append(f"        return {pascal}.model_validate(resp.json())")
    lines.append("")

    # list
    lines.append(f"    async def list_{schema_name}s(")
    lines.append(f"        self, skip: int = 0, limit: int = 100,")
    lines.append(f"        where: dict[str, Any] | None = None,")
    lines.append(f"        sort: str | None = None,")
    lines.append(f"    ) -> list[{pascal}]:")
    lines.append(f'        """List {pascal} entities."""')
    lines.append(f"        params: dict[str, Any] = {{'skip': skip, 'limit': limit}}")
    lines.append(f"        if where:")
    lines.append(f"            import json")
    lines.append(f"            params['where'] = json.dumps(where)")
    lines.append(f"        if sort:")
    lines.append(f"            params['sort'] = sort")
    lines.append(
        f'        resp = await self._client.get("/{schema_name}/", params=params)'
    )
    lines.append(f"        resp.raise_for_status()")
    lines.append(
        f"        return [{pascal}.model_validate(item) for item in resp.json()]"
    )
    lines.append("")

    # update
    lines.append(f"    async def update_{schema_name}(")
    lines.append(f"        self, entity_id: str | UUID, data: {pascal}Update")
    lines.append(f"    ) -> {pascal}:")
    lines.append(f'        """Update a {pascal}."""')
    lines.append(
        f'        resp = await self._client.patch(f"/{schema_name}/{{entity_id}}", json=data.model_dump(exclude_unset=True))'
    )
    lines.append(f"        resp.raise_for_status()")
    lines.append(f"        return {pascal}.model_validate(resp.json())")
    lines.append("")

    # delete
    lines.append(f"    async def delete_{schema_name}(")
    lines.append(f"        self, entity_id: str | UUID")
    lines.append(f"    ) -> {pascal}:")
    lines.append(f'        """Delete a {pascal}."""')
    lines.append(
        f'        resp = await self._client.delete(f"/{schema_name}/{{entity_id}}")'
    )
    lines.append(f"        resp.raise_for_status()")
    lines.append(f"        return {pascal}.model_validate(resp.json())")
    lines.append("")

    return "\n".join(lines)
