"""Content negotiation filter — JSON / YAML / XML interchangeably.

Deserializes incoming request bodies from YAML or XML into JSON (so FastAPI
can parse them normally), and re-serializes outgoing JSON responses into the
format requested via the ``Accept`` header.

Requires optional dependencies:
- ``pyyaml`` for YAML support (``pip install slip-stream[yaml]``)
- ``xmltodict`` for XML support (``pip install slip-stream[xml]``)
"""

from __future__ import annotations

import json
import logging
from typing import Any

from starlette.requests import Request
from starlette.responses import Response

from slip_stream.adapters.api.filters.base import FilterBase, FilterContext

logger = logging.getLogger(__name__)

_YAML_TYPES = frozenset({"application/yaml", "application/x-yaml", "text/yaml"})
_XML_TYPES = frozenset({"application/xml", "text/xml"})
_JSON_TYPES = frozenset({"application/json", "", "*/*"})


def _parse_media_type(header_value: str) -> str:
    """Extract the media type from a header value, ignoring parameters."""
    return header_value.split(";")[0].strip().lower()


def _load_yaml(text: str) -> Any:
    """Parse YAML text, raising ImportError if pyyaml is not installed."""
    try:
        import yaml
    except ImportError as exc:
        raise ImportError(
            "YAML support requires pyyaml. Install with: "
            "pip install slip-stream[yaml]"
        ) from exc
    return yaml.safe_load(text)


def _dump_yaml(data: Any) -> str:
    """Serialize data to YAML, raising ImportError if pyyaml is not installed."""
    try:
        import yaml
    except ImportError as exc:
        raise ImportError(
            "YAML support requires pyyaml. Install with: "
            "pip install slip-stream[yaml]"
        ) from exc
    return yaml.dump(data, default_flow_style=False, allow_unicode=True)


def _load_xml(text: str) -> Any:
    """Parse XML text, raising ImportError if xmltodict is not installed."""
    try:
        import xmltodict
    except ImportError as exc:
        raise ImportError(
            "XML support requires xmltodict. Install with: "
            "pip install slip-stream[xml]"
        ) from exc
    return xmltodict.parse(text)


def _dump_xml(data: Any, root_tag: str = "response") -> str:
    """Serialize data to XML, raising ImportError if xmltodict is not installed."""
    try:
        import xmltodict
    except ImportError as exc:
        raise ImportError(
            "XML support requires xmltodict. Install with: "
            "pip install slip-stream[xml]"
        ) from exc
    # xmltodict.unparse needs a dict with exactly one root key
    if isinstance(data, list):
        data = {root_tag: {"item": data}}
    elif not isinstance(data, dict):
        data = {root_tag: data}
    else:
        data = {root_tag: data}
    return xmltodict.unparse(data, pretty=True)


class ContentNegotiationFilter(FilterBase):
    """Filter that transparently converts between JSON, YAML, and XML.

    On request:
        Reads ``Content-Type``; if YAML or XML, reads the raw body, converts
        it to a JSON dict, and replaces the ASGI receive callable so FastAPI
        sees a JSON body.

    On response:
        Reads ``Accept`` header; if YAML or XML, deserializes the JSON
        response body and re-serializes it in the requested format.

    Attributes:
        order: 50 (runs after auth filters, before user filters).
    """

    order: int = 50

    async def on_request(self, request: Request, context: FilterContext) -> None:
        content_type = _parse_media_type(
            request.headers.get("content-type", "application/json")
        )
        accept = _parse_media_type(request.headers.get("accept", "application/json"))

        context.content_type = content_type
        context.accept = accept

        if content_type in _JSON_TYPES:
            return

        body = await request.body()
        if not body:
            return

        body_text = body.decode("utf-8")

        if content_type in _YAML_TYPES:
            parsed = _load_yaml(body_text)
        elif content_type in _XML_TYPES:
            parsed = _load_xml(body_text)
            # xmltodict wraps in a root element — unwrap single root
            if isinstance(parsed, dict) and len(parsed) == 1:
                parsed = next(iter(parsed.values()))
        else:
            return

        json_body = json.dumps(parsed).encode("utf-8")

        # Replace the cached body so subsequent request.body() calls
        # return the converted JSON
        request._body = json_body

        # Also replace the receive callable for any code that reads
        # from the ASGI stream directly
        async def receive():  # noqa: ANN202
            return {"type": "http.request", "body": json_body}

        request._receive = receive

        # Override Content-Type in scope headers so FastAPI parses as JSON
        scope = request.scope
        raw_headers = [
            (k, v) if k != b"content-type" else (k, b"application/json")
            for k, v in scope.get("headers", [])
        ]
        scope["headers"] = raw_headers

    async def on_response(
        self, request: Request, response: Response, context: FilterContext
    ) -> Response:
        accept = context.accept

        if accept in _JSON_TYPES:
            return response

        # Read the response body — StreamingResponse from call_next()
        # only has body_iterator, not body.
        body = b""
        if hasattr(response, "body_iterator"):
            chunks = []
            async for chunk in response.body_iterator:
                if isinstance(chunk, str):
                    chunks.append(chunk.encode("utf-8"))
                else:
                    chunks.append(chunk)
            body = b"".join(chunks)
        elif hasattr(response, "body"):
            body = response.body

        if not body:
            return response

        try:
            data = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return response

        if accept in _YAML_TYPES:
            serialized = _dump_yaml(data)
            media_type = "application/yaml"
        elif accept in _XML_TYPES:
            serialized = _dump_xml(data)
            media_type = "application/xml"
        else:
            return response

        # Copy headers but remove content-type/content-length since we're
        # replacing the body with a different format
        new_headers = {
            k: v
            for k, v in response.headers.items()
            if k.lower() not in ("content-type", "content-length")
        }

        return Response(
            content=serialized,
            status_code=response.status_code,
            headers=new_headers,
            media_type=media_type,
        )
