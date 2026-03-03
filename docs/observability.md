# Observability

slip-stream auto-mounts three operational endpoints during application startup. No configuration needed — they appear as soon as you use `SlipStream.lifespan()`.

## Health Probe

```
GET /health
```

Liveness check. Always returns 200.

```json
{"status": "healthy"}
```

Use this for Kubernetes `livenessProbe` or load balancer health checks.

## Readiness Probe

```
GET /ready
```

Checks that the application is ready to serve requests:

- **Database**: Pings MongoDB (or passes if using an external `get_db`)
- **Schemas**: Verifies at least one schema is loaded in the registry

**Ready (200):**
```json
{
  "status": "ready",
  "checks": {
    "database": true,
    "schemas": true
  }
}
```

**Not ready (503):**
```json
{
  "status": "not_ready",
  "checks": {
    "database": false,
    "schemas": true
  }
}
```

Use this for Kubernetes `readinessProbe` or deployment verification.

## Topology Endpoint

```
GET /_topology
```

Returns the full application structure as JSON — useful for AI agents and dev tools to understand a running app without reading source code.

**Does NOT expose** database URIs, credentials, or environment variables.

```json
{
  "schemas": [
    {
      "name": "pet",
      "storage_backend": "mongo",
      "versions": ["1.0.0"],
      "has_custom_handler": {
        "create": false,
        "get": false,
        "list": false,
        "update": false,
        "delete": false
      },
      "has_custom_repository": false,
      "has_custom_controller": false,
      "endpoints": {
        "rest": "/api/v1/pet/",
        "graphql": false
      }
    },
    {
      "name": "order",
      "storage_backend": "mongo",
      "versions": ["1.0.0"],
      "has_custom_handler": {
        "create": true,
        "get": false,
        "list": false,
        "update": false,
        "delete": true
      },
      "has_custom_repository": false,
      "has_custom_controller": false,
      "endpoints": {
        "rest": "/api/v1/order/",
        "graphql": false
      }
    }
  ],
  "filters": [
    {"name": "ResponseEnvelopeFilter", "order": 90},
    {"name": "FieldProjectionFilter", "order": 95}
  ],
  "config": {
    "api_prefix": "/api/v1",
    "graphql_enabled": false,
    "graphql_prefix": "/graphql",
    "schema_vending_enabled": false,
    "structured_errors": true,
    "storage_default": "mongo"
  }
}
```

### What Topology Reveals

| Section | Contents |
|---------|----------|
| `schemas` | Every registered entity with storage backend, versions, and whether handlers/repos/controllers are custom |
| `filters` | Active ASGI filters sorted by execution order |
| `config` | API prefix, feature flags (GraphQL, schema vending, structured errors), default storage |

### What Topology Does NOT Reveal

- Database connection strings
- API keys or secrets
- Environment variables
- Internal file paths

## Filter Exclusions

Health, readiness, and topology endpoints are automatically excluded from:

- **Rate limiting** — `/health`, `/ready`, and `/_topology` are in the default `skip_paths`
- **Rego policy** — `/ready` and `/_topology` are in the default `skip_paths`

This ensures operational endpoints remain accessible even when auth or rate limiting is enabled.

## Example: Deployment Verification Script

```bash
# Wait for app to be ready
until curl -sf http://localhost:8000/ready; do
  echo "Waiting for app..."
  sleep 2
done

echo "App is ready!"

# Inspect what's running
curl -s http://localhost:8000/_topology | python -m json.tool
```
