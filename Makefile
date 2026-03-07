.PHONY: test lint typecheck coverage check docs docs-serve clean integration integration-up integration-down integration-test \
	bench-perf bench-load bench-breaking bench-constrained bench-limits bench-compat bench-compat-docker bench-compare-docker bench-clean

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

# --- Benchmark targets ---

# Run perf baseline against local MongoDB (native)
bench-perf:
	./benchmarks/measure.sh --app slip-stream --port 8100 --scenario smoke

# Run perf load test against local MongoDB (native)
bench-load:
	./benchmarks/measure.sh --app slip-stream --port 8100 --scenario load

# Run stress test (ramp to failure)
bench-breaking:
	./benchmarks/measure.sh --app slip-stream --port 8100 --scenario breaking

# Run constrained stress test (Docker-based resource limits)
bench-constrained:
	docker compose -f benchmarks/docker-compose.yml up -d
	docker compose -f benchmarks/docker-compose.yml --profile test run \
		-e K6_SCENARIO=breaking k6-perf
	docker compose -f benchmarks/docker-compose.yml down -v

# Run perf test with specific CPU/MEM limits (Docker)
# Usage: make bench-limits SLIP_CPU=0.5 SLIP_MEM=256m
bench-limits:
	SLIP_CPU=$(SLIP_CPU) SLIP_MEM=$(SLIP_MEM) \
	docker compose -f benchmarks/docker-compose.yml up -d
	docker compose -f benchmarks/docker-compose.yml --profile test run \
		-e K6_SCENARIO=$(or $(K6_SCENARIO),load) k6-perf
	docker compose -f benchmarks/docker-compose.yml down -v

# Run compatibility tests (requires both apps running)
# Start stellar-drive on :8200 separately, then run this
bench-compat:
	k6 run benchmarks/k6/compat_test.js

# Run compatibility tests via Docker (both apps containerized)
bench-compat-docker:
	docker compose -f benchmarks/docker-compose.compat.yml up -d --build
	docker compose -f benchmarks/docker-compose.compat.yml --profile test run k6-compat; \
	status=$$?; \
	docker compose -f benchmarks/docker-compose.compat.yml down -v; \
	exit $$status

# Run both apps' perf tests side-by-side (Docker)
bench-compare-docker:
	docker compose -f benchmarks/docker-compose.compat.yml up -d --build
	docker compose -f benchmarks/docker-compose.compat.yml --profile perf run k6-perf-slip & \
	docker compose -f benchmarks/docker-compose.compat.yml --profile perf run k6-perf-stellar & \
	wait
	docker compose -f benchmarks/docker-compose.compat.yml down -v

# Clean benchmark results
bench-clean:
	rm -f benchmarks/results/*.json

# Clean build artifacts
clean: bench-clean
	rm -rf dist/ build/ *.egg-info .coverage htmlcov/ docs/api-html/ .pytest_cache/ .mypy_cache/
