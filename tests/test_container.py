"""Tests for EntityContainer."""

import pytest
from pydantic import BaseModel

from slip_stream.container import EntityContainer, get_container, init_container
from slip_stream.core.domain.base import BaseDocument


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


class TestTryImport:
    """Tests for the _try_import helper."""

    def test_try_import_missing_module_returns_none(self):
        """_try_import returns None when the module doesn't exist."""
        from slip_stream.container import _try_import

        result = _try_import("slip_stream.nonexistent_module_xyz", "SomeClass")
        assert result is None

    def test_try_import_attribute_error_returns_none(self, monkeypatch):
        """_try_import returns None when the module exists but the attribute is missing."""
        import sys
        import types

        from slip_stream.container import _try_import

        # Create a real module with no attributes of the expected name
        fake_module = types.ModuleType("slip_stream._test_fake_module_attr")
        monkeypatch.setitem(
            sys.modules, "slip_stream._test_fake_module_attr", fake_module
        )

        result = _try_import("slip_stream._test_fake_module_attr", "MissingAttribute")
        assert result is None

    def test_try_import_found_returns_attribute(self, monkeypatch):
        """_try_import returns the attribute when module and attr both exist."""
        import sys
        import types

        from slip_stream.container import _try_import

        sentinel = object()
        fake_module = types.ModuleType("slip_stream._test_fake_found")
        fake_module.MyClass = sentinel  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "slip_stream._test_fake_found", fake_module)

        result = _try_import("slip_stream._test_fake_found", "MyClass")
        assert result is sentinel


class TestResolveVersion:
    """Tests for EntityContainer.resolve_version()."""

    def test_resolve_returns_default_models_when_no_version(self, container):
        """resolve_version with version=None returns the default model triple."""
        doc, create, update = container.resolve_version("widget", version=None)
        reg = container.get("widget")
        assert doc is reg.document_model
        assert create is reg.create_model
        assert update is reg.update_model

    def test_resolve_latest_returns_default_models(self, container):
        """resolve_version('latest') returns the default model triple."""
        doc, create, update = container.resolve_version("widget", version="latest")
        reg = container.get("widget")
        assert doc is reg.document_model
        assert create is reg.create_model
        assert update is reg.update_model

    def test_resolve_version_cached(self, container):
        """Calling resolve_version twice for the same version uses the cache."""

        reg_entry = container.get("widget")
        # Populate the version_models cache manually
        sentinel_triple = (
            reg_entry.document_model,
            reg_entry.create_model,
            reg_entry.update_model,
        )
        reg_entry.version_models["1.0.0"] = sentinel_triple

        result = container.resolve_version("widget", version="1.0.0")
        assert result is sentinel_triple


class TestCustomOverrides:
    """Tests for custom model/repository/service discovery."""

    def test_custom_models_override(self, monkeypatch, registry):
        """Container uses hand-crafted models when found in models_module."""
        import sys
        import types

        from pydantic import BaseModel

        from slip_stream.core.domain.base import BaseDocument

        # Create fake model classes
        class WidgetCustomDoc(BaseDocument):
            pass

        class WidgetCustomCreate(BaseModel):
            pass

        class WidgetCustomUpdate(BaseModel):
            pass

        fake_models = types.ModuleType("myapp.models")
        fake_models.Widget = WidgetCustomDoc  # type: ignore[attr-defined]
        fake_models.WidgetCreate = WidgetCustomCreate  # type: ignore[attr-defined]
        fake_models.WidgetUpdate = WidgetCustomUpdate  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "myapp.models", fake_models)

        container = EntityContainer(models_module="myapp.models")
        container.resolve_all(registry.get_schema_names())

        reg = container.get("widget")
        assert reg.document_model is WidgetCustomDoc
        assert reg.create_model is WidgetCustomCreate
        assert reg.update_model is WidgetCustomUpdate

    def test_custom_repository_override(self, monkeypatch, registry):
        """Container uses custom repository class when found in repositories_module."""
        import sys
        import types

        class CustomWidgetRepository:
            def __init__(self, db):
                self.db = db

        fake_repo_module = types.ModuleType("myapp.persistence.widget_repository")
        fake_repo_module.WidgetRepository = CustomWidgetRepository  # type: ignore[attr-defined]
        monkeypatch.setitem(
            sys.modules,
            "myapp.persistence.widget_repository",
            fake_repo_module,
        )

        container = EntityContainer(repositories_module="myapp.persistence")
        container.resolve_all(registry.get_schema_names())

        reg = container.get("widget")
        assert reg.repository_class is CustomWidgetRepository

    def test_custom_service_override(self, monkeypatch, registry):
        """Container uses custom service class for an operation when found."""
        import sys
        import types

        class WidgetCreateService:
            pass

        fake_service_module = types.ModuleType("myapp.services.widget_service")
        fake_service_module.WidgetCreateService = WidgetCreateService  # type: ignore[attr-defined]
        monkeypatch.setitem(
            sys.modules,
            "myapp.services.widget_service",
            fake_service_module,
        )

        container = EntityContainer(services_module="myapp.services")
        container.resolve_all(registry.get_schema_names())

        reg = container.get("widget")
        assert reg.services["create"] is WidgetCreateService

    def test_controller_factory_discovery(self, monkeypatch, registry):
        """Container sets controller_factory when create_router is found."""
        import sys
        import types

        def create_router():
            pass

        fake_ctrl_module = types.ModuleType("myapp.controllers.widget_controller")
        fake_ctrl_module.create_router = create_router  # type: ignore[attr-defined]
        monkeypatch.setitem(
            sys.modules,
            "myapp.controllers.widget_controller",
            fake_ctrl_module,
        )

        container = EntityContainer(controllers_module="myapp.controllers")
        container.resolve_all(registry.get_schema_names())

        reg = container.get("widget")
        assert reg.controller_factory is create_router
