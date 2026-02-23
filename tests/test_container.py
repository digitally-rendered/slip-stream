"""Tests for EntityContainer."""

import pytest
from pydantic import BaseModel

from slip_stream.container import EntityContainer, init_container, get_container
from slip_stream.core.domain.base import BaseDocument
from slip_stream.core.schema.registry import SchemaRegistry


@pytest.fixture
def container(registry):
    """Create and populate a container with sample schemas."""
    container = EntityContainer()
    container.resolve_all(registry.get_schema_names())
    return container


class TestEntityContainer:
    """Tests for entity resolution and registration."""

    def test_resolve_all_registers_schemas(self, container):
        """resolve_all() registers all discovered schemas."""
        registrations = container.get_all()
        assert "widget" in registrations

    def test_get_returns_registration(self, container):
        """get() returns an EntityRegistration for a known schema."""
        reg = container.get("widget")
        assert reg.schema_name == "widget"
        assert issubclass(reg.document_model, BaseDocument)
        assert issubclass(reg.create_model, BaseModel)
        assert issubclass(reg.update_model, BaseModel)

    def test_get_unknown_raises_key_error(self, container):
        """get() raises KeyError for unknown schemas."""
        with pytest.raises(KeyError):
            container.get("nonexistent")

    def test_registration_has_services(self, container):
        """Each registration has all 5 service operations."""
        reg = container.get("widget")
        assert "create" in reg.services
        assert "get" in reg.services
        assert "list" in reg.services
        assert "update" in reg.services
        assert "delete" in reg.services

    def test_registration_has_repository_class(self, container):
        """Each registration has a repository class."""
        reg = container.get("widget")
        assert reg.repository_class is not None

    def test_controller_factory_is_none_by_default(self, container):
        """controller_factory is None when no custom controller exists."""
        reg = container.get("widget")
        assert reg.controller_factory is None


class TestContainerSingleton:
    """Tests for the module-level singleton helpers."""

    def test_init_and_get_container(self, registry):
        """init_container() creates and stores a singleton."""
        import slip_stream.container as mod

        # Clear any existing singleton
        mod._container = None

        c = init_container(schema_names=registry.get_schema_names())
        assert c is get_container()

    def test_get_container_before_init_raises(self):
        """get_container() raises RuntimeError if not initialized."""
        import slip_stream.container as mod

        mod._container = None
        with pytest.raises(RuntimeError, match="not been initialised"):
            get_container()
