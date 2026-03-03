# Testing Rules

## Absolute Rules
- **NEVER skip tests.** `@pytest.mark.skip`, `@pytest.mark.xfail`, `pytest.skip()` are FORBIDDEN.
- **NEVER use `# type: ignore` to hide test failures.**
- **Fix failing tests, don't skip them.**

## Testing Stack
- `pytest` + `pytest-asyncio` for async test support
- `mongomock-motor` for MongoDB mocking (NOT testcontainers, NOT real MongoDB)

## Test Structure
```
tests/
├── conftest.py              # Shared fixtures (mongomock-motor DB, schema registry reset)
├── sample_schemas/          # JSON schemas for testing
│   └── widget.json
├── test_schema_registry.py
├── test_versioned_crud.py
├── test_endpoint_factory.py
└── test_container.py
```

## Key Test Patterns

### SchemaRegistry Reset for Test Isolation
```python
@pytest.fixture(autouse=True)
def reset_schema_registry():
    SchemaRegistry.reset()
    yield
    SchemaRegistry.reset()
```

### Testing VersionedMongoCRUD
```python
async def test_versioned_create(crud_instance):
    result = await crud_instance.create(create_data)
    assert result.record_version == 1
    assert result.entity_id is not None
    assert result.deleted_at is None
```

### Testing Soft Delete
```python
async def test_soft_delete(crud_instance):
    created = await crud_instance.create(create_data)
    deleted = await crud_instance.delete_by_entity_id(created.entity_id)
    assert deleted.deleted_at is not None
    assert deleted.record_version == 2
    result = await crud_instance.get_by_entity_id(created.entity_id)
    assert result is None
```

## Running Tests
```bash
poetry run pytest tests/ -x -q          # All tests, stop on first failure
poetry run pytest tests/ --cov=slip_stream  # With coverage
```
