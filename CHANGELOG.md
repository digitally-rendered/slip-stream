# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Pre-commit hooks: ruff (auto-fix), black (format), mypy (typecheck), pytest
- Coverage threshold enforcement (75% minimum)
- `py.typed` marker for PEP 561 typed package support
- Dependabot for automated dependency updates
- Schema contract tests for schema-to-endpoint round-trip validation
- Performance benchmarks for schema parsing, model generation, CRUD operations
- OpenAPI backward compatibility checking
- MongoDB integration tests against real database in CI
- CONTRIBUTING.md and SECURITY.md

### Fixed
- All mypy type errors resolved across 75 source files (no blanket `ignore_errors` overrides)
- Unused imports and variables cleaned up across test suite

## [0.1.0] - 2026-02-23

### Added
- Core framework: JSON Schema-driven auto-generation of Pydantic models, MongoDB CRUD, and FastAPI endpoints
- Hexagonal architecture with clean layer separation (core, driving adapters, driven adapters)
- Versioned document pattern: append-only MongoDB persistence with soft deletes
- `SchemaRegistry` for JSON Schema discovery and Pydantic model generation
- `EntityContainer` with 4-layer override system (models, repositories, services, controllers)
- `SlipStreamRegistry` decorator API (`@handler`, `@guard`, `@validate`, `@transform`, `@on`)
- `EventBus` with pre/post lifecycle hooks per CRUD operation
- `OperationExecutor` shared between REST and GraphQL transports
- `RequestContext` dataclass with channel-scoped decorators
- Filter chain: ASGI middleware with `AuthFilter`, `ContentNegotiationFilter`, `ResponseEnvelopeFilter`, `FieldProjectionFilter`, `SchemaVersionFilter`
- Schema versioning with semver, multi-backend storage (`FileSchemaStorage`, `MongoSchemaStorage`, `HttpSchemaStorage`, `CompositeSchemaStorage`)
- Schema vending API with DAG endpoint
- `SchemaWatcher` for live schema reload
- GraphQL API via Strawberry with full lifecycle parity
- Query DSL for MongoDB-style filtering through REST and GraphQL
- RFC 7807 Problem Details structured error responses
- Health probes (`/health`, `/ready`) and topology endpoint (`/_topology`)
- MCP server with 11 tools for AI agent integration (read + write)
- SDK generator for typed Python clients
- SQL adapter via SQLAlchemy (optional)
- Policy engine with OPA integration (optional)
- Audit trail and webhook dispatcher
- Event streaming with `StreamAdapter`, `InMemoryStream`, `EventStreamBridge`
- Rate limiting middleware
- CLI (`slip init`, `slip schema add`, `slip schema list`, `slip schema validate`, `slip run`)
- Schemathesis-based fuzz testing with hex-architecture invariant checks
- Comprehensive documentation (10 guides)
- CLAUDE.md generation for new projects

[Unreleased]: https://github.com/digitally-rendered/slip-stream/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/digitally-rendered/slip-stream/releases/tag/v0.1.0
