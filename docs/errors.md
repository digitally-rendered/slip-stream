# Structured Errors (RFC 7807)

When `structured_errors=True`, all error responses use the [RFC 7807 Problem Details](https://datatracker.ietf.org/doc/html/rfc7807) format with `Content-Type: application/problem+json`.

## Enabling

```python
slip = SlipStream(
    app=app,
    schema_dir=Path("./schemas"),
    structured_errors=True,
)
```

## Response Format

Every error response contains these fields:

```json
{
  "type": "https://slip-stream.dev/errors/not-found",
  "title": "Not Found",
  "status": 404,
  "detail": "widget not found",
  "instance": "/api/v1/widget/abc123"
}
```

| Field | Description |
|-------|-------------|
| `type` | URI identifying the error type |
| `title` | Short human-readable summary |
| `status` | HTTP status code |
| `detail` | Specific explanation for this occurrence |
| `instance` | The request path that triggered the error |

## Error Type Map

| Status | Type URI Slug | Title |
|--------|---------------|-------|
| 400 | `bad-request` | Bad Request |
| 403 | `policy-denied` | Policy Denied |
| 404 | `not-found` | Not Found |
| 409 | `conflict` | Conflict |
| 422 | `validation-error` | Validation Error |
| 429 | `rate-limited` | Rate Limited |
| 500 | `internal-error` | Internal Server Error |
| 503 | `service-unavailable` | Service Unavailable |

All type URIs use the base `https://slip-stream.dev/errors/`.

## Validation Errors

422 responses include an `errors` extension member with Pydantic validation details:

```json
{
  "type": "https://slip-stream.dev/errors/validation-error",
  "title": "Validation Error",
  "status": 422,
  "detail": "Validation error",
  "instance": "/api/v1/pet/",
  "errors": [
    {
      "loc": ["body", "name"],
      "msg": "Field required",
      "type": "missing"
    }
  ]
}
```

## Filter Integration

Error responses flow through the filter chain. This means:

- **Content negotiation** works on errors — request `Accept: application/yaml` and errors come back as YAML
- **Response envelope** wraps errors the same way it wraps success responses
- Filter **short-circuit** responses (from `AuthFilter`, `RateLimitFilter`, etc.) also use RFC 7807 format

## Examples

```bash
# 404 — entity not found
curl http://localhost:8000/api/v1/widget/nonexistent
```

```json
{
  "type": "https://slip-stream.dev/errors/not-found",
  "title": "Not Found",
  "status": 404,
  "detail": "widget not found",
  "instance": "/api/v1/widget/nonexistent"
}
```

```bash
# 422 — validation error (missing required field)
curl -X POST http://localhost:8000/api/v1/pet/ \
  -H "Content-Type: application/json" \
  -d '{}'
```

```json
{
  "type": "https://slip-stream.dev/errors/validation-error",
  "title": "Validation Error",
  "status": 422,
  "detail": "Validation error",
  "instance": "/api/v1/pet/",
  "errors": [
    {
      "loc": ["body", "name"],
      "msg": "Field required",
      "type": "missing"
    }
  ]
}
```

```bash
# 403 — guard rejection (from @registry.guard)
curl -X DELETE http://localhost:8000/api/v1/pet/some-id \
  -H "X-User-ID: viewer"
```

```json
{
  "type": "https://slip-stream.dev/errors/policy-denied",
  "title": "Policy Denied",
  "status": 403,
  "detail": "Admin role required",
  "instance": "/api/v1/pet/some-id"
}
```

## Without Structured Errors

When `structured_errors=False` (the default), FastAPI's built-in error handling applies — plain `{"detail": "..."}` responses.
