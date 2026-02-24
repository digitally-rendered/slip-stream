# Contributing to slip-stream

Thank you for your interest in contributing to slip-stream.

## Getting Started

```bash
# Clone the repository
git clone https://github.com/draw/slip-stream.git
cd slip-stream

# Install dependencies (requires Poetry and Python 3.12+)
poetry install --all-extras

# Install pre-commit hooks
poetry run pre-commit install

# Verify everything works
make check
```

## Development Workflow

### Before You Code

1. Check existing [issues](https://github.com/draw/slip-stream/issues) for related work
2. For non-trivial changes, open an issue first to discuss the approach
3. Create a feature branch from `main`

### While You Code

- **Run `make check`** before committing — it runs lint, typecheck, and tests with coverage
- Pre-commit hooks auto-fix formatting (ruff + black) and enforce mypy + pytest
- If a pre-commit hook modifies files, re-stage and commit again

### Code Standards

| Tool | What it enforces | Config |
|------|-----------------|--------|
| **ruff** | Linting (E/F/W/I rules), import sorting | `pyproject.toml [tool.ruff]` |
| **black** | Code formatting (88 char line length) | `pyproject.toml [tool.black]` |
| **mypy** | Static type checking | `pyproject.toml [tool.mypy]` |
| **pytest** | Tests must pass, 75% coverage minimum | `pyproject.toml [tool.coverage]` |

### Architecture Rules

slip-stream uses hexagonal architecture. These rules are enforced:

- **Core MUST NOT import from Adapters.** `slip_stream/core/` must never import from `slip_stream/adapters/`
- **Adapters MUST NOT import from each other.** API adapters must not directly import persistence adapters
- **Dependencies flow inward.** External frameworks (FastAPI, Motor) stay in the adapter layer

### Testing

```bash
make test              # Quick test run
make coverage          # Tests with coverage enforcement
make bench             # Run performance benchmarks
```

- Use `mongomock-motor` for database mocking, not real MongoDB
- Reset `SchemaRegistry` in fixtures (autouse fixture in `conftest.py`)
- Never use `@pytest.mark.skip` or `@pytest.mark.xfail`
- Tests using `register_schema()` must use `tmp_path`, not the shared `schema_dir` fixture

### Adding a New Feature

1. If it touches the schema pipeline: add a schema contract test in `tests/test_schema_contracts.py`
2. If it adds a public symbol: add it to `slip_stream/__init__.py` `__all__`
3. If it affects performance-critical paths: add a benchmark in `tests/benchmarks/`
4. Update `CHANGELOG.md` under `[Unreleased]`

## Pull Request Process

1. Ensure `make check` passes (lint + types + tests + coverage)
2. Update `CHANGELOG.md` with your changes
3. Write a clear PR description explaining **why**, not just what
4. PRs require passing CI on Python 3.12 and 3.13

## Commit Messages

Use clear, imperative-mood commit messages:

```
Add schema migration support for v2 schemas
Fix race condition in SchemaWatcher debounce
Update ResponseEnvelopeFilter to include total_count
```

## Reporting Issues

- **Bugs**: Include Python version, slip-stream version, minimal reproduction, and full traceback
- **Features**: Describe the use case, not just the solution
- **Security**: See [SECURITY.md](SECURITY.md) for vulnerability reporting
