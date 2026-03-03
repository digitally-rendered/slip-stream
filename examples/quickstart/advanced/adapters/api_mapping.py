"""Config-driven external API mapping — bridges external OpenAPI specs to local schemas.

This module demonstrates the target pattern: take an external service's
OpenAPI spec and configure how its object model maps to the local domain
schema. The adapter reads this mapping and handles translation automatically.

The mapping is defined in YAML configuration::

    external_apis:
      shipstation:
        spec_url: https://ssapi.shipstation.com/openapi.json
        auth:
          type: basic
          key_env: SHIPSTATION_API_KEY
          secret_env: SHIPSTATION_API_SECRET
        mappings:
          - local_schema: order
            remote_resource: /orders/createorder
            remote_method: POST
            trigger: post_create
            field_map:
              entity_id: orderNumber       # UUID → string
              quantity: items[0].quantity
              status: orderStatus
              pet_name: items[0].name       # from ctx.extras["pet_doc"]
            response_map:
              tracking_number: shipmentId
              carrier: carrierCode
              estimated_days: estimatedDays
            constants:
              orderStatus: awaiting_shipment
              items[0].unitPrice: 0

      thedogapi:
        spec_url: https://api.thedogapi.com/v1
        auth:
          type: header
          header_name: x-api-key
          key_env: DOG_API_KEY
        mappings:
          - local_schema: pet
            remote_resource: /breeds/search
            remote_method: GET
            trigger: post_get
            query_params:
              q: name                      # local field → query param
            response_map:
              breed_group: breed_group
              temperament: temperament
              life_span: life_span
              origin: origin

This approach means:
1. Drop an external OpenAPI spec URL in config
2. Define field-level mappings from local schema → remote schema
3. The adapter auto-generates the translation layer
4. No hand-written adapter code needed for simple integrations
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration model
# ---------------------------------------------------------------------------


@dataclass
class AuthConfig:
    """Authentication configuration for an external API."""

    type: str  # "basic", "header", "bearer", "none"
    key_env: str | None = None
    secret_env: str | None = None
    header_name: str | None = None


@dataclass
class FieldMapping:
    """Maps a single local field to a remote field path.

    Supports dotted paths (``items[0].name``) and array indexing.
    """

    local_field: str
    remote_path: str


@dataclass
class EndpointMapping:
    """Maps one local schema operation to an external API endpoint."""

    local_schema: str
    remote_resource: str
    remote_method: str = "POST"
    trigger: str = "post_create"  # slip-stream lifecycle event
    field_map: dict[str, str] = field(default_factory=dict)
    response_map: dict[str, str] = field(default_factory=dict)
    query_params: dict[str, str] = field(default_factory=dict)
    constants: dict[str, Any] = field(default_factory=dict)


@dataclass
class ExternalApiConfig:
    """Configuration for one external API integration."""

    name: str
    spec_url: str | None = None
    base_url: str | None = None
    auth: AuthConfig = field(default_factory=lambda: AuthConfig(type="none"))
    mappings: list[EndpointMapping] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Field path utilities
# ---------------------------------------------------------------------------


def _set_nested(obj: dict, path: str, value: Any) -> None:
    """Set a value in a nested dict using dotted path notation.

    Supports array indexing: ``items[0].name`` creates the structure
    ``{"items": [{"name": value}]}``.

    >>> d = {}
    >>> _set_nested(d, "items[0].name", "Fido")
    >>> d
    {'items': [{'name': 'Fido'}]}
    """
    parts = path.replace("[", ".[").split(".")
    current = obj
    for i, part in enumerate(parts[:-1]):
        if part.startswith("[") and part.endswith("]"):
            idx = int(part[1:-1])
            while len(current) <= idx:
                current.append({})
            current = current[idx]
        else:
            next_part = parts[i + 1] if i + 1 < len(parts) else ""
            if next_part.startswith("["):
                current.setdefault(part, [])
            else:
                current.setdefault(part, {})
            current = current[part]

    last = parts[-1]
    if last.startswith("[") and last.endswith("]"):
        idx = int(last[1:-1])
        while len(current) <= idx:
            current.append(None)
        current[idx] = value
    else:
        current[last] = value


def _get_nested(obj: dict | list, path: str) -> Any:
    """Get a value from a nested dict using dotted path notation.

    >>> _get_nested({"a": {"b": [{"c": 42}]}}, "a.b[0].c")
    42
    """
    parts = path.replace("[", ".[").split(".")
    current = obj
    for part in parts:
        if current is None:
            return None
        if part.startswith("[") and part.endswith("]"):
            idx = int(part[1:-1])
            if isinstance(current, list) and len(current) > idx:
                current = current[idx]
            else:
                return None
        elif isinstance(current, dict):
            current = current.get(part)
        else:
            return None
    return current


# ---------------------------------------------------------------------------
# Config-driven adapter
# ---------------------------------------------------------------------------


class MappedApiAdapter:
    """Generic adapter that translates domain objects to/from external APIs
    using declarative field mappings.

    This replaces hand-written adapters for simple REST integrations.
    Complex cases (retry logic, pagination, auth flows) still need
    custom adapters, but the mapping layer can be reused.

    Example::

        config = ExternalApiConfig(
            name="shipstation",
            base_url="https://ssapi.shipstation.com",
            auth=AuthConfig(type="basic", key_env="SS_KEY", secret_env="SS_SECRET"),
            mappings=[
                EndpointMapping(
                    local_schema="order",
                    remote_resource="/orders/createorder",
                    field_map={"entity_id": "orderNumber", "quantity": "items[0].quantity"},
                    response_map={"tracking_number": "shipmentId"},
                )
            ],
        )

        adapter = MappedApiAdapter(config)
        result = await adapter.call(
            mapping=config.mappings[0],
            local_data={"entity_id": "abc-123", "quantity": 2},
        )
        # result == {"tracking_number": "SHIP-456"}
    """

    def __init__(self, config: ExternalApiConfig) -> None:
        self.config = config
        self._client = self._build_client(config)

    @staticmethod
    def _build_client(config: ExternalApiConfig) -> httpx.AsyncClient:
        """Build an HTTP client with the configured authentication."""
        base_url = config.base_url or ""
        kwargs: dict[str, Any] = {
            "base_url": base_url,
            "timeout": 30.0,
            "headers": {"Content-Type": "application/json"},
        }

        auth = config.auth
        if auth.type == "basic":
            key = os.environ.get(auth.key_env or "", "")
            secret = os.environ.get(auth.secret_env or "", "")
            kwargs["auth"] = (key, secret)
        elif auth.type == "header" and auth.header_name:
            key = os.environ.get(auth.key_env or "", "")
            kwargs["headers"][auth.header_name] = key
        elif auth.type == "bearer":
            key = os.environ.get(auth.key_env or "", "")
            kwargs["headers"]["Authorization"] = f"Bearer {key}"

        return httpx.AsyncClient(**kwargs)

    def translate_outbound(
        self, mapping: EndpointMapping, local_data: dict[str, Any]
    ) -> dict[str, Any]:
        """Transform local domain data → external API payload.

        Uses the ``field_map`` to translate field names and the
        ``constants`` to inject static values.
        """
        payload: dict[str, Any] = {}

        # Apply field mappings
        for local_field, remote_path in mapping.field_map.items():
            value = local_data.get(local_field)
            if value is not None:
                _set_nested(
                    payload, remote_path, str(value) if hasattr(value, "hex") else value
                )

        # Apply constants
        for remote_path, value in mapping.constants.items():
            _set_nested(payload, remote_path, value)

        return payload

    def translate_inbound(
        self, mapping: EndpointMapping, remote_data: dict[str, Any]
    ) -> dict[str, Any]:
        """Transform external API response → local domain data.

        Uses the ``response_map`` to extract and rename fields.
        """
        result: dict[str, Any] = {}

        for local_field, remote_path in mapping.response_map.items():
            value = _get_nested(remote_data, remote_path)
            if value is not None:
                result[local_field] = value

        return result

    async def call(
        self,
        mapping: EndpointMapping,
        local_data: dict[str, Any],
    ) -> dict[str, Any]:
        """Execute the full roundtrip: translate → call → translate back."""
        if mapping.remote_method.upper() == "GET":
            # Build query params from local data
            params = {}
            for param_name, local_field in mapping.query_params.items():
                value = local_data.get(local_field)
                if value is not None:
                    params[param_name] = str(value)

            response = await self._client.get(mapping.remote_resource, params=params)
        else:
            payload = self.translate_outbound(mapping, local_data)
            response = await self._client.request(
                mapping.remote_method.upper(),
                mapping.remote_resource,
                json=payload,
            )

        response.raise_for_status()
        remote_data = response.json()

        # If response is a list, take the first item
        if isinstance(remote_data, list):
            remote_data = remote_data[0] if remote_data else {}

        return self.translate_inbound(mapping, remote_data)

    async def close(self) -> None:
        await self._client.aclose()


# ---------------------------------------------------------------------------
# Helper: Load config from YAML
# ---------------------------------------------------------------------------


def load_api_configs(config_data: dict[str, Any]) -> list[ExternalApiConfig]:
    """Parse the ``external_apis`` section of a slip-stream.yml file.

    Example YAML structure::

        external_apis:
          shipstation:
            base_url: https://ssapi.shipstation.com
            auth:
              type: basic
              key_env: SS_KEY
              secret_env: SS_SECRET
            mappings:
              - local_schema: order
                remote_resource: /orders/createorder
                field_map:
                  entity_id: orderNumber
    """
    configs = []
    apis = config_data.get("external_apis", {})

    for name, api_data in apis.items():
        auth_data = api_data.get("auth", {})
        auth = AuthConfig(
            type=auth_data.get("type", "none"),
            key_env=auth_data.get("key_env"),
            secret_env=auth_data.get("secret_env"),
            header_name=auth_data.get("header_name"),
        )

        mappings = []
        for m in api_data.get("mappings", []):
            mappings.append(
                EndpointMapping(
                    local_schema=m["local_schema"],
                    remote_resource=m["remote_resource"],
                    remote_method=m.get("remote_method", "POST"),
                    trigger=m.get("trigger", "post_create"),
                    field_map=m.get("field_map", {}),
                    response_map=m.get("response_map", {}),
                    query_params=m.get("query_params", {}),
                    constants=m.get("constants", {}),
                )
            )

        configs.append(
            ExternalApiConfig(
                name=name,
                spec_url=api_data.get("spec_url"),
                base_url=api_data.get("base_url"),
                auth=auth,
                mappings=mappings,
            )
        )

    return configs
