# Filters

Filters are ASGI-level middleware that intercept requests and responses before FastAPI processes them. They execute in an onion model — request filters run in ascending order, response filters run in descending order.

## How Filters Work

```
Request → [Filter 10] → [Filter 50] → [Filter 90] → FastAPI Endpoint
                                                          ↓
Response ← [Filter 10] ← [Filter 50] ← [Filter 90] ← Response
```

Each filter has an `order` attribute. Lower values run first on request and last on response.

## Using Filters

Pass filter instances to `SlipStream`:

```python
from slip_stream import (
    SlipStream,
    AuthFilter,
    ContentNegotiationFilter,
    ResponseEnvelopeFilter,
    FieldProjectionFilter,
)

slip = SlipStream(
    app=app,
    schema_dir=Path("./schemas"),
    filters=[
        AuthFilter(authenticate=my_auth_fn),   # order=10
        ContentNegotiationFilter(),             # order=50
        ResponseEnvelopeFilter(),               # order=90
        FieldProjectionFilter(),                # order=95
    ],
)
```

Filters are sorted by `order` automatically — pass them in any order.

## Built-in Filters

### AuthFilter (order=10)

Token-based authentication. Delegates to a custom `authenticate` callable.

```python
from slip_stream import AuthFilter

async def my_authenticate(token: str) -> dict | None:
    """Return user dict if valid, None if invalid."""
    user = await verify_token(token)
    return {"id": user.id, "role": user.role} if user else None

auth = AuthFilter(
    authenticate=my_authenticate,
    realm="myapp",  # WWW-Authenticate realm (default: "slip-stream")
)
```

- Reads `Authorization: Bearer <token>` header
- Returns 401 with `WWW-Authenticate` header if missing/invalid
- Sets `FilterContext.user` on success (flows into `RequestContext.current_user`)

### ContentNegotiationFilter (order=50)

Automatic JSON/YAML/XML conversion based on `Content-Type` and `Accept` headers.

```python
from slip_stream import ContentNegotiationFilter

filter = ContentNegotiationFilter()
```

**Request conversion**: Parses YAML/XML request bodies into JSON for FastAPI:

```bash
# Send YAML
curl -X POST /api/v1/pet/ \
  -H "Content-Type: application/yaml" \
  -d "name: Buddy\nstatus: available"

# Send XML
curl -X POST /api/v1/pet/ \
  -H "Content-Type: application/xml" \
  -d "<pet><name>Buddy</name><status>available</status></pet>"
```

**Response conversion**: Re-serializes JSON responses based on `Accept` header:

```bash
# Get YAML response
curl /api/v1/pet/ -H "Accept: application/yaml"

# Get XML response
curl /api/v1/pet/ -H "Accept: application/xml"
```

Requires optional dependencies: `pip install slip-stream[yaml]` or `pip install slip-stream[xml]`.

### ResponseEnvelopeFilter (order=90)

Wraps responses in a standardized envelope with metadata.

```python
from slip_stream import ResponseEnvelopeFilter

filter = ResponseEnvelopeFilter(include_pagination=True)
```

**Single entity response:**
```json
{
  "data": {"name": "Buddy", "status": "available"},
  "meta": {
    "request_id": "550e8400-e29b-41d4-a716-446655440000"
  }
}
```

**List response (with pagination):**
```json
{
  "data": [
    {"name": "Buddy"},
    {"name": "Max"}
  ],
  "meta": {
    "request_id": "550e8400-e29b-41d4-a716-446655440000",
    "pagination": {
      "skip": 0,
      "limit": 100,
      "count": 2,
      "total_count": 42,
      "has_more": true
    }
  }
}
```

- Skips 4xx/5xx error responses and 204 No Content
- Pagination auto-detected for list endpoints (reads `?skip=` and `?limit=` query params)
- `total_count` — total matching entities (not just the current page)
- `has_more` — `true` when there are more results beyond the current page
- Set `include_pagination=False` to disable pagination metadata

### FieldProjectionFilter (order=95)

Controls which fields appear in responses.

```python
from slip_stream import FieldProjectionFilter

filter = FieldProjectionFilter(
    role_field_rules={
        "pet": {
            "admin": {"name", "status", "category", "entity_id", "created_at"},
            "viewer": {"name", "status", "entity_id"},
            "*": {"name", "entity_id"},  # fallback for unknown roles
        }
    },
    allow_query_projection=True,
)
```

**Query parameter projection:**
```bash
# Only return name and status fields
GET /api/v1/pet/?fields=name,status
```

**Role-based projection**: Restricts fields per role per schema. When both query params and role rules are active, uses their intersection (query params cannot expose fields hidden by role config).

**Envelope-aware**: If the response is wrapped in `{data, meta}`, projection applies to the `data` portion only.

## Writing Custom Filters

Extend `FilterBase` and implement `on_request` and `on_response`:

```python
from slip_stream import FilterBase, FilterContext
from starlette.requests import Request
from starlette.responses import Response

class RequestTimingFilter(FilterBase):
    order = 5  # Runs very early on request, very late on response

    async def on_request(self, request: Request, context: FilterContext) -> None:
        import time
        context.extras["start_time"] = time.monotonic()

    async def on_response(
        self, request: Request, response: Response, context: FilterContext
    ) -> Response:
        import time
        elapsed = time.monotonic() - context.extras["start_time"]
        response.headers["X-Response-Time"] = f"{elapsed:.3f}s"
        return response
```

### FilterContext

Per-request state shared across all filters in the chain:

```python
@dataclass
class FilterContext:
    content_type: str = "application/json"
    accept: str = "application/json"
    user: dict | None = None
    extras: dict = field(default_factory=dict)
```

- `user` is populated by `AuthFilter` and flows into `RequestContext.current_user`
- `extras` is a free-form dict for filter-to-filter communication

### Short-Circuiting

Raise `FilterShortCircuit` from `on_request` to abort and return an error response immediately:

```python
from slip_stream import FilterBase, FilterShortCircuit

class RateLimitFilter(FilterBase):
    order = 5

    async def on_request(self, request, context):
        if self._is_rate_limited(request):
            raise FilterShortCircuit(
                status_code=429,
                body="Rate limit exceeded",
                headers={"Retry-After": "60"},
            )

    async def on_response(self, request, response, context):
        return response
```

Short-circuit responses still flow through response filters (for content negotiation).

## Filter Order Convention

| Filter | Order | Purpose |
|--------|-------|---------|
| Custom (early) | 1-9 | Timing, rate limiting |
| AuthFilter | 10 | Authentication |
| ContentNegotiationFilter | 50 | Format conversion |
| ResponseEnvelopeFilter | 90 | Response wrapping |
| FieldProjectionFilter | 95 | Field visibility |
| Custom (late) | 100+ | Custom response transforms |
