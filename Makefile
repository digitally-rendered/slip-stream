.PHONY: test lint typecheck coverage check docs docs-serve clean integration integration-up integration-down integration-test

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

# Start integration test services (MongoDB + PostgreSQL)
integration-up:
	docker compose up -d --wait

# Stop integration test services
integration-down:
	docker compose down -v

# Run integration tests (assumes services are already up)
integration-test:
	MONGO_URI=mongodb://localhost:27017 \
	DATABASE_URL=postgresql+asyncpg://slip_stream_test:slip_stream_test@localhost:$${PG_PORT:-5432}/slip_stream_test \
	poetry run pytest tests/integration/ -x -v

# Full integration cycle: start services, run tests, stop services
integration: integration-up
	$(MAKE) integration-test; status=$$?; $(MAKE) integration-down; exit $$status

# Clean build artifacts
clean:
	rm -rf dist/ build/ *.egg-info .coverage htmlcov/ docs/api-html/ .pytest_cache/ .mypy_cache/
