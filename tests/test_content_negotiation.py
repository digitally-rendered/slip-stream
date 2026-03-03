"""Tests for ContentNegotiationFilter (JSON / YAML / XML)."""

import pytest
import xmltodict
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from slip_stream.adapters.api.filters.chain import FilterChain
from slip_stream.adapters.api.filters.content_negotiation import (
    ContentNegotiationFilter,
)
from slip_stream.adapters.api.filters.middleware import FilterChainMiddleware


def _create_app(max_body_size: int = 1_048_576) -> FastAPI:
    """Create a test app with the content negotiation filter."""
    app = FastAPI()

    @app.post("/echo")
    async def echo(data: dict):
        return data

    @app.get("/item")
    async def get_item():
        return {"id": "123", "name": "Widget", "active": True}

    @app.get("/items")
    async def get_items():
        return [{"id": "1", "name": "A"}, {"id": "2", "name": "B"}]

    chain = FilterChain()
    chain.add_filter(ContentNegotiationFilter(max_body_size=max_body_size))
    app.add_middleware(FilterChainMiddleware, filter_chain=chain)

    return app


@pytest.fixture
def client():
    return TestClient(_create_app())


class TestContentNegotiationRequest:
    """Tests for request body deserialization."""

    def test_json_passthrough(self, client):
        response = client.post(
            "/echo",
            json={"name": "test"},
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 200
        assert response.json()["name"] == "test"

    def test_yaml_request_body(self, client):
        yaml_body = yaml.dump({"name": "from-yaml", "count": 42})
        response = client.post(
            "/echo",
            content=yaml_body,
            headers={"Content-Type": "application/yaml"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "from-yaml"
        assert data["count"] == 42

    def test_yaml_x_type(self, client):
        yaml_body = yaml.dump({"name": "x-yaml"})
        response = client.post(
            "/echo",
            content=yaml_body,
            headers={"Content-Type": "application/x-yaml"},
        )
        assert response.status_code == 200
        assert response.json()["name"] == "x-yaml"

    def test_xml_request_body(self, client):
        xml_body = xmltodict.unparse(
            {"item": {"name": "from-xml", "count": "7"}}, pretty=True
        )
        response = client.post(
            "/echo",
            content=xml_body,
            headers={"Content-Type": "application/xml"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "from-xml"
        assert data["count"] == "7"


class TestContentNegotiationResponse:
    """Tests for response body serialization."""

    def test_json_response_default(self, client):
        response = client.get("/item")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/json")
        assert response.json()["name"] == "Widget"

    def test_yaml_response(self, client):
        response = client.get(
            "/item",
            headers={"Accept": "application/yaml"},
        )
        assert response.status_code == 200
        assert "yaml" in response.headers["content-type"]
        data = yaml.safe_load(response.text)
        assert data["name"] == "Widget"
        assert data["active"] is True

    def test_xml_response(self, client):
        response = client.get(
            "/item",
            headers={"Accept": "application/xml"},
        )
        assert response.status_code == 200
        assert "xml" in response.headers["content-type"]
        parsed = xmltodict.parse(response.text)
        assert "response" in parsed
        item = parsed["response"]
        assert item["name"] == "Widget"

    def test_yaml_list_response(self, client):
        response = client.get(
            "/items",
            headers={"Accept": "application/yaml"},
        )
        assert response.status_code == 200
        data = yaml.safe_load(response.text)
        assert isinstance(data, list)
        assert len(data) == 2
        assert data[0]["name"] == "A"

    def test_xml_list_response(self, client):
        response = client.get(
            "/items",
            headers={"Accept": "application/xml"},
        )
        assert response.status_code == 200
        parsed = xmltodict.parse(response.text)
        assert "response" in parsed
        # List is wrapped as {response: {item: [...]}}
        items = parsed["response"]["item"]
        assert isinstance(items, list)
        assert len(items) == 2


class TestContentNegotiationRoundTrip:
    """Tests for sending non-JSON and receiving non-JSON."""

    def test_yaml_in_yaml_out(self, client):
        yaml_body = yaml.dump({"name": "round-trip"})
        response = client.post(
            "/echo",
            content=yaml_body,
            headers={
                "Content-Type": "application/yaml",
                "Accept": "application/yaml",
            },
        )
        assert response.status_code == 200
        data = yaml.safe_load(response.text)
        assert data["name"] == "round-trip"

    def test_xml_in_yaml_out(self, client):
        xml_body = xmltodict.unparse({"item": {"name": "cross-format"}}, pretty=True)
        response = client.post(
            "/echo",
            content=xml_body,
            headers={
                "Content-Type": "application/xml",
                "Accept": "application/yaml",
            },
        )
        assert response.status_code == 200
        data = yaml.safe_load(response.text)
        assert data["name"] == "cross-format"


# ---------------------------------------------------------------------------
# Request body size limit
# ---------------------------------------------------------------------------


class TestBodySizeLimit:

    def test_body_over_limit_returns_413(self):
        client = TestClient(_create_app(max_body_size=100))
        large_body = yaml.dump({"name": "x" * 200})
        response = client.post(
            "/echo",
            content=large_body,
            headers={"Content-Type": "application/yaml"},
        )
        assert response.status_code == 413

    def test_body_under_limit_passes(self):
        client = TestClient(_create_app(max_body_size=10_000))
        yaml_body = yaml.dump({"name": "small"})
        response = client.post(
            "/echo",
            content=yaml_body,
            headers={"Content-Type": "application/yaml"},
        )
        assert response.status_code == 200

    def test_custom_max_body_size(self):
        client = TestClient(_create_app(max_body_size=50))
        # Body under 50 bytes
        yaml_body = yaml.dump({"a": "b"})
        response = client.post(
            "/echo",
            content=yaml_body,
            headers={"Content-Type": "application/yaml"},
        )
        if len(yaml_body.encode()) <= 50:
            assert response.status_code == 200
        else:
            assert response.status_code == 413


# ---------------------------------------------------------------------------
# XML safety (defusedxml)
# ---------------------------------------------------------------------------


class TestXmlSafety:

    def test_xml_entity_expansion_blocked(self):
        """If defusedxml is installed, entity expansion is blocked."""
        try:
            import defusedxml  # noqa: F401
        except ImportError:
            pytest.skip("defusedxml not installed")

        # Use raise_server_exceptions=False so we get the 500 response
        # instead of an unhandled exception propagation
        client = TestClient(_create_app(), raise_server_exceptions=False)
        # XML bomb — billion laughs attack (simplified)
        xml_bomb = (
            '<?xml version="1.0"?>'
            "<!DOCTYPE lolz ["
            '  <!ENTITY lol "lol">'
            '  <!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">'
            "]>"
            "<item><name>&lol2;</name></item>"
        )
        response = client.post(
            "/echo",
            content=xml_bomb,
            headers={"Content-Type": "application/xml"},
        )
        # xmltodict/defusedxml rejects entity expansion — results in 500
        assert response.status_code >= 400
