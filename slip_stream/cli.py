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
# Shared utilities (re-exported from schema_utils for CLI use)
# ---------------------------------------------------------------------------

from slip_stream.schema_utils import (
    SCHEMA_TEMPLATE as _SCHEMA_TEMPLATE,
    create_schema_file,
    snake_case as _snake_case,
    title_case as _title_case,
    validate_all_schemas,
)

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

_CLAUDE_MD_TEMPLATE = """\
# {project_title}

Powered by [slip-stream](https://pypi.org/project/slip-stream/) — a JSON Schema-driven backend framework for FastAPI + MongoDB.

## How It Works

Drop a JSON schema file in `schemas/`, restart the server, and slip-stream auto-generates:
1. **Pydantic models** (Document, Create, Update) from the schema
2. **MongoDB CRUD operations** with append-only versioning
3. **FastAPI endpoints** (POST, GET, GET list, PATCH, DELETE)

## Project Structure

```
{project_name}/
  schemas/          # JSON Schema definitions (add new entities here)
  main.py           # FastAPI application entry point
  .env              # Environment variables (MONGO_URI, DATABASE_NAME)
  CLAUDE.md         # This file — AI agent instructions
```

## Adding a New Schema

```bash
slip schema add my_entity       # Creates schemas/my_entity.json
slip schema list                 # List all discovered schemas
slip schema validate             # Validate all schemas
slip run                         # Start dev server with auto-reload
```

Or create `schemas/my_entity.json` manually with `title`, `version`, `type: "object"`, and `properties`.

## API Conventions

- **Base URL**: http://localhost:8000/api/v1/
- **Entity path**: Schema name in kebab-case (`user_profile` -> `/api/v1/user-profile/`)
- **Endpoints per entity**:
  - `POST   /api/v1/<entity>/`              — Create
  - `GET    /api/v1/<entity>/`              — List (`?skip=`, `?limit=`, `?where=`, `?sort=`)
  - `GET    /api/v1/<entity>/{{entity_id}}` — Get by ID
  - `PATCH  /api/v1/<entity>/{{entity_id}}` — Update
  - `DELETE /api/v1/<entity>/{{entity_id}}` — Soft delete
- **Health**: `GET /health` (liveness), `GET /ready` (readiness)
- **Topology**: `GET /_topology` (app structure as JSON)
- **Docs**: `GET /docs` (Swagger UI)

## Customizing with Decorators

```python
from slip_stream import SlipStreamRegistry, HookError, RequestContext

registry = SlipStreamRegistry()

@registry.guard("my_entity", "delete")
async def admins_only(ctx: RequestContext) -> None:
    if ctx.current_user.get("role") != "admin":
        raise HookError(403, "Admin role required")

@registry.validate("my_entity", "create", "update")
async def check_name(ctx: RequestContext) -> None:
    if not ctx.data.name:
        raise HookError(422, "Name is required")

@registry.handler("my_entity", "create")
async def custom_create(ctx: RequestContext):
    # Full control over create logic
    ...
```

## Key Technical Details

- **Versioned documents**: Every write creates a new version (append-only, never mutate)
- **Soft deletes**: DELETE creates a tombstone record with `deleted_at` set
- **UUID fields**: `entity_id` is stable across versions; `id` is unique per version
- **Python ^3.12**, **FastAPI**, **Motor** (async MongoDB), **Pydantic v2**
"""


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

    # Write CLAUDE.md
    claude_md = project_dir / "CLAUDE.md"
    claude_md.write_text(
        _CLAUDE_MD_TEMPLATE.format(
            project_title=_title_case(_snake_case(project_name)),
            project_name=project_name,
        )
    )
    print(f"  Created {claude_md}")

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

    try:
        target = create_schema_file(root / "schemas", args.name, args.description)
    except FileExistsError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    name = _snake_case(args.name)
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
    results = validate_all_schemas(schemas_dir)
    if not results:
        print("No schemas found.")
        return 0

    errors = 0
    for fname, issues in results.items():
        if issues:
            print(f"  FAIL  {fname}: {', '.join(issues)}")
            errors += 1
        else:
            # Read version for display
            try:
                data = json.loads((schemas_dir / fname).read_text())
                version = data.get("version", "?")
            except Exception:
                version = "?"
            print(f"  OK    {fname} (v{version})")

    print()
    if errors:
        print(f"{errors} schema(s) failed validation.")
        return 1
    print(f"All {len(results)} schema(s) valid.")
    return 0


def cmd_schema_test(args: argparse.Namespace) -> int:
    """Run schemathesis property-based tests against all schemas."""
    root = _find_project_root()
    if root is None:
        print("Error: cannot find project root (no schemas/ directory found).", file=sys.stderr)
        return 1

    try:
        from slip_stream.testing.runner import SchemaTestRunner
    except ImportError:
        print(
            "Error: schemathesis is required for schema testing.\n"
            "Install it with: pip install slip-stream[test]",
            file=sys.stderr,
        )
        return 1

    schemas_dir = root / "schemas"
    mode = getattr(args, "mode", "lifecycle")

    print(f"Running schema tests from {schemas_dir}...")
    print()

    runner = SchemaTestRunner(schema_dir=schemas_dir)

    if mode == "lifecycle":
        results = runner.run_lifecycle_tests()
        runner.print_results(results)
        failed = sum(1 for r in results if not r.passed)
        return 1 if failed else 0
    elif mode == "fuzz":
        return runner.run_schemathesis_cli(
            checks=getattr(args, "checks", "not_a_server_error"),
        )
    else:
        # Run both
        print("=== Lifecycle Tests ===")
        print()
        results = runner.run_lifecycle_tests()
        runner.print_results(results)

        failed = sum(1 for r in results if not r.passed)
        if failed:
            print(f"\n{failed} lifecycle test(s) failed. Skipping fuzz tests.")
            return 1

        print("\n=== Schemathesis Fuzzing ===")
        print()
        return runner.run_schemathesis_cli(
            checks=getattr(args, "checks", "not_a_server_error"),
        )


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

    # slip schema test
    test_p = schema_sub.add_parser(
        "test", help="Run property-based tests against all schemas"
    )
    test_p.add_argument(
        "--mode",
        choices=["lifecycle", "fuzz", "all"],
        default="lifecycle",
        help="Test mode: lifecycle (CRUD), fuzz (schemathesis), or all (default: lifecycle)",
    )
    test_p.add_argument(
        "--checks",
        default="not_a_server_error",
        help="Comma-separated schemathesis checks (default: not_a_server_error)",
    )

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
        elif args.schema_command == "test":
            return cmd_schema_test(args)
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
