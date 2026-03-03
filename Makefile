.PHONY: test lint typecheck coverage check docs docs-serve clean

# Run tests
test:
	poetry run pytest tests/ --tb=short -q

# Run tests with coverage enforcement
coverage:
	poetry run pytest tests/ --cov=slip_stream --cov-report=term-missing --cov-fail-under=75 -q

# Lint with ruff (auto-fix) + black (format)
lint:
	poetry run ruff check --fix slip_stream/ tests/
	poetry run black slip_stream/ tests/

# Static type checking
typecheck:
	poetry run mypy slip_stream/

# Run all quality checks (same as CI)
check: lint typecheck coverage

# Run pre-commit on all files
pre-commit:
	poetry run pre-commit run --all-files

# Generate API docs
docs:
	poetry run pdoc slip_stream --output-directory docs/api-html
	@echo "API docs generated in docs/api-html/"

# Serve API docs locally
docs-serve:
	poetry run pdoc slip_stream

# Snapshot OpenAPI spec for backward compatibility checks
snapshot-api:
	poetry run python -c "\
	from slip_stream.testing.app_builder import build_test_app; \
	from pathlib import Path; \
	import json; \
	app = build_test_app(schema_dir=Path('tests/sample_schemas')); \
	spec = app.openapi(); \
	Path('.openapi-baseline.json').write_text(json.dumps(spec, indent=2, sort_keys=True))"
	@echo "OpenAPI baseline saved to .openapi-baseline.json"

# Run benchmarks
bench:
	poetry run pytest tests/benchmarks/ -v --benchmark-only

# Clean build artifacts
clean:
	rm -rf dist/ build/ *.egg-info .coverage htmlcov/ docs/api-html/ .pytest_cache/ .mypy_cache/
