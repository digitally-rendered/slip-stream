"""slip-stream CLI.

Provides project scaffolding and development utilities::

    slip init myproject          # Scaffold a new project
    slip schema add widget       # Add a new JSON Schema
    slip schema list             # List discovered schemas
    slip schema validate         # Validate all schemas
    slip run                     # Start dev server with auto-reload

Requires no external CLI libraries — uses only the stdlib ``argparse``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Sequence


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------

_SCHEMA_TEMPLATE = """\
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

_MAIN_TEMPLATE = '''\
"""FastAPI application powered by slip-stream.

Run with:
    uvicorn main:app --reload

Then visit http://localhost:8000/docs
"""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from slip_stream import SlipStream

SCHEMAS_DIR = Path(__file__).parent / "schemas"


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    slip = SlipStream(
        app=FastAPI(),
        schema_dir=SCHEMAS_DIR,
        api_prefix="/api/v1",
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with slip.lifespan():
            yield

    app = FastAPI(
        title="{project_title}",
        description="API powered by slip-stream.",
        version="0.1.0",
        lifespan=lifespan,
    )
    slip.app = app

    return app


app = create_app()
'''

_ENV_TEMPLATE = """\
MONGO_URI=mongodb://localhost:27017
DATABASE_NAME={db_name}
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _snake_case(name: str) -> str:
    """Normalise a name to snake_case for file/schema naming."""
    import re

    s = re.sub(r"[^a-zA-Z0-9]", "_", name)
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", s)
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s)
    return s.lower().strip("_")


def _title_case(snake: str) -> str:
    """Convert a snake_case string to TitleCase."""
    return "".join(word.capitalize() for word in snake.split("_"))


def _find_project_root(start: Path | None = None) -> Path | None:
    """Walk up from *start* looking for a schemas/ directory."""
    p = (start or Path.cwd()).resolve()
    while True:
        if (p / "schemas").is_dir():
            return p
        if p.parent == p:
            return None
        p = p.parent


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_init(args: argparse.Namespace) -> int:
    """Scaffold a new slip-stream project."""
    project_dir = Path(args.directory).resolve()
    project_name = project_dir.name

    if project_dir.exists() and any(project_dir.iterdir()):
        print(f"Error: {project_dir} already exists and is not empty.", file=sys.stderr)
        return 1

    schemas_dir = project_dir / "schemas"
    schemas_dir.mkdir(parents=True, exist_ok=True)
    print(f"  Created {schemas_dir}/")

    # Write main.py
    main_py = project_dir / "main.py"
    main_py.write_text(
        _MAIN_TEMPLATE.format(project_title=_title_case(_snake_case(project_name)))
    )
    print(f"  Created {main_py}")

    # Write .env
    env_file = project_dir / ".env"
    env_file.write_text(
        _ENV_TEMPLATE.format(db_name=_snake_case(project_name) + "_db")
    )
    print(f"  Created {env_file}")

    # Write a sample schema
    sample = schemas_dir / "item.json"
    sample.write_text(
        _SCHEMA_TEMPLATE.format(
            title="Item",
            description="A sample item.",
            title_lower="item",
        )
    )
    print(f"  Created {sample}")

    print(f"\nProject '{project_name}' initialised.")
    print(f"\n  cd {project_dir}")
    print("  pip install slip-stream")
    print("  uvicorn main:app --reload")
    return 0


def cmd_schema_add(args: argparse.Namespace) -> int:
    """Add a new JSON Schema file."""
    root = _find_project_root()
    if root is None:
        print("Error: cannot find project root (no schemas/ directory found).", file=sys.stderr)
        return 1

    name = _snake_case(args.name)
    title = _title_case(name)
    schemas_dir = root / "schemas"

    target = schemas_dir / f"{name}.json"
    if target.exists():
        print(f"Error: {target} already exists.", file=sys.stderr)
        return 1

    description = args.description or f"A {title} entity."
    target.write_text(
        _SCHEMA_TEMPLATE.format(
            title=title,
            description=description,
            title_lower=title.lower(),
        )
    )
    print(f"  Created {target}")
    print(f"  Endpoints will be available at /api/v1/{name}/")
    return 0


def cmd_schema_list(args: argparse.Namespace) -> int:
    """List schemas discovered in the project."""
    root = _find_project_root()
    if root is None:
        print("Error: cannot find project root (no schemas/ directory found).", file=sys.stderr)
        return 1

    schemas_dir = root / "schemas"
    files = sorted(schemas_dir.glob("**/*.json"))
    if not files:
        print("No schemas found.")
        return 0

    print(f"Schemas in {schemas_dir}:\n")
    for f in files:
        try:
            data = json.loads(f.read_text())
            title = data.get("title", "?")
            version = data.get("version", "?")
            name = f.stem
            rel = f.relative_to(schemas_dir)
            print(f"  {name:<20} v{version:<10} {title}  ({rel})")
        except (json.JSONDecodeError, KeyError):
            print(f"  {str(f.relative_to(schemas_dir)):<20} (invalid JSON)")
    return 0


def cmd_schema_validate(args: argparse.Namespace) -> int:
    """Validate all schemas in the project."""
    root = _find_project_root()
    if root is None:
        print("Error: cannot find project root (no schemas/ directory found).", file=sys.stderr)
        return 1

    schemas_dir = root / "schemas"
    files = sorted(schemas_dir.glob("**/*.json"))
    if not files:
        print("No schemas found.")
        return 0

    errors = 0
    for f in files:
        name = f.relative_to(schemas_dir)
        try:
            data = json.loads(f.read_text())
        except json.JSONDecodeError as e:
            print(f"  FAIL  {name}: invalid JSON — {e}")
            errors += 1
            continue

        issues: list[str] = []
        if "title" not in data:
            issues.append("missing 'title'")
        if "version" not in data:
            issues.append("missing 'version'")
        if data.get("type") != "object":
            issues.append("'type' must be 'object'")
        if "properties" not in data:
            issues.append("missing 'properties'")

        if issues:
            print(f"  FAIL  {name}: {', '.join(issues)}")
            errors += 1
        else:
            print(f"  OK    {name} (v{data['version']})")

    print()
    if errors:
        print(f"{errors} schema(s) failed validation.")
        return 1
    print(f"All {len(files)} schema(s) valid.")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    """Start the development server."""
    root = _find_project_root()
    if root is None:
        print("Error: cannot find project root (no schemas/ directory found).", file=sys.stderr)
        return 1

    app_module = args.app or "main:app"
    host = args.host or "127.0.0.1"
    port = str(args.port or 8000)

    cmd = [
        sys.executable, "-m", "uvicorn",
        app_module,
        "--host", host,
        "--port", port,
        "--reload",
        "--reload-dir", str(root),
    ]

    print(f"Starting slip-stream dev server...")
    print(f"  App:  {app_module}")
    print(f"  URL:  http://{host}:{port}")
    print(f"  Docs: http://{host}:{port}/docs")
    print()

    try:
        return subprocess.call(cmd, cwd=str(root))
    except KeyboardInterrupt:
        return 0


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """Build and return the top-level argument parser with all sub-commands registered."""
    parser = argparse.ArgumentParser(
        prog="slip",
        description="slip-stream CLI — JSON Schema-driven backend framework",
    )
    sub = parser.add_subparsers(dest="command")

    # slip init
    init_p = sub.add_parser("init", help="Scaffold a new project")
    init_p.add_argument("directory", help="Project directory to create")

    # slip schema (with sub-commands)
    schema_p = sub.add_parser("schema", help="Schema management commands")
    schema_sub = schema_p.add_subparsers(dest="schema_command")

    # slip schema add
    add_p = schema_sub.add_parser("add", help="Add a new JSON Schema")
    add_p.add_argument("name", help="Schema name (e.g., widget, user_profile)")
    add_p.add_argument("-d", "--description", help="Schema description")

    # slip schema list
    schema_sub.add_parser("list", help="List discovered schemas")

    # slip schema validate
    schema_sub.add_parser("validate", help="Validate all schemas")

    # slip run
    run_p = sub.add_parser("run", help="Start development server")
    run_p.add_argument("--app", help="App module (default: main:app)")
    run_p.add_argument("--host", default="127.0.0.1", help="Host (default: 127.0.0.1)")
    run_p.add_argument("--port", type=int, default=8000, help="Port (default: 8000)")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "init":
        return cmd_init(args)
    elif args.command == "schema":
        if args.schema_command == "add":
            return cmd_schema_add(args)
        elif args.schema_command == "list":
            return cmd_schema_list(args)
        elif args.schema_command == "validate":
            return cmd_schema_validate(args)
        else:
            parser.parse_args(["schema", "--help"])
            return 1
    elif args.command == "run":
        return cmd_run(args)
    else:
        parser.print_help()
        return 0


if __name__ == "__main__":
    sys.exit(main())
