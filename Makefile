.PHONY: test docs docs-serve lint

test:
	poetry run pytest --tb=short -q

docs:
	poetry run pdoc slip_stream --output-directory docs/api-html
	@echo "API docs generated in docs/api-html/"

docs-serve:
	poetry run pdoc slip_stream

lint:
	poetry run ruff check slip_stream tests
	poetry run black --check slip_stream tests
