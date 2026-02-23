# Case Convention Rules

## Python Code
- **Variables, functions, methods:** `snake_case`
- **Classes:** `PascalCase`
- **Constants:** `UPPER_SNAKE_CASE`
- **File names:** `snake_case.py`
- **JSON schema files:** `snake_case.json`

## API Layer
- **URL paths:** `kebab-case` (e.g., `/api/v1/labor-market-analysis/`)
- **Request/response bodies:** `snake_case` (Python convention, FastAPI default)
- **Query parameters:** `snake_case`

## Schema Name to Path Mapping
JSON schema file names use `snake_case` but API paths use `kebab-case`:
- Schema: `labor_market_analysis.json` -> Path: `/api/v1/labor-market-analysis/`
- This conversion is automatic: `schema_name.replace("_", "-")`
