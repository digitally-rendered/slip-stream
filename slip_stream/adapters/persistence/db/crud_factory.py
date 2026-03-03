"""CRUD Factory for generating CRUD classes from schemas."""

from typing import Any, Callable, Type, cast

from motor.motor_asyncio import AsyncIOMotorDatabase

from slip_stream.adapters.persistence.db.generic_crud import VersionedMongoCRUD
from slip_stream.core.schema.registry import SchemaRegistry


class CRUDFactory:
    """Factory for creating CRUD classes from schemas."""

    @classmethod
    def create_crud_class(
        cls, schema_name: str, version: str = "latest"
    ) -> Type[VersionedMongoCRUD[Any, Any, Any]]:
        """Create a CRUD class for a schema.

        Args:
            schema_name: Name of the schema.
            version: Schema version or ``"latest"``.

        Returns:
            A CRUD class extending VersionedMongoCRUD.
        """
        registry = SchemaRegistry()

        document_model = registry.generate_document_model(schema_name, version)
        create_model = registry.generate_create_model(schema_name, version)
        update_model = registry.generate_update_model(schema_name, version)

        class GeneratedCRUD(
            VersionedMongoCRUD[document_model, create_model, update_model]  # type: ignore[valid-type]
        ):
            """Generated CRUD class for schema."""

            def __init__(self, db: AsyncIOMotorDatabase) -> None:
                super().__init__(
                    db=db,
                    collection_name=schema_name,
                    model=document_model,
                    create_model=create_model,
                    update_model=update_model,
                )

        crud_class_name = (
            f"{schema_name.replace('_', ' ').title().replace(' ', '_')}CRUD"
        )
        GeneratedCRUD.__name__ = crud_class_name

        GeneratedCRUD.collection_name = schema_name  # type: ignore[attr-defined]
        GeneratedCRUD.document_model = document_model  # type: ignore[attr-defined]
        GeneratedCRUD.create_model = create_model  # type: ignore[attr-defined, misc]
        GeneratedCRUD.update_model = update_model  # type: ignore[attr-defined, misc]

        return GeneratedCRUD  # type: ignore[return-value]

    @classmethod
    def create_crud_instance(
        cls, db: AsyncIOMotorDatabase, schema_name: str, version: str = "latest"
    ) -> VersionedMongoCRUD[Any, Any, Any]:
        """Create a CRUD instance for a schema.

        Args:
            db: MongoDB database connection.
            schema_name: Name of the schema.
            version: Schema version or ``"latest"``.

        Returns:
            A CRUD instance.
        """
        crud_class = cls.create_crud_class(schema_name, version)
        factory_fn = cast(
            Callable[[AsyncIOMotorDatabase], VersionedMongoCRUD[Any, Any, Any]],
            crud_class,
        )
        return factory_fn(db)
