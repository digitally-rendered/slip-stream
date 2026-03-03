"""OpenTelemetry instrumentation for slip-stream.

Provides automatic tracing of CRUD operations, lifecycle hooks, and HTTP
requests without requiring changes to consumer code.

Usage::

    slip = SlipStream(app=app, schema_dir=..., telemetry=True)

Or manual::

    from slip_stream.telemetry import SlipStreamInstrumentor
    instrumentor = SlipStreamInstrumentor()
    instrumentor.instrument_operation_executor()
    instrumentor.instrument_event_bus(event_bus)

Install the optional dependency::

    pip install slip-stream[telemetry]
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Optional

logger = logging.getLogger(__name__)

try:
    from opentelemetry import trace
    from opentelemetry.trace import Status, StatusCode

    HAS_OTEL = True
except ImportError:
    HAS_OTEL = False

if TYPE_CHECKING:
    pass

# Names of OperationExecutor methods to wrap
_EXECUTOR_METHODS = [
    "execute_create",
    "execute_get",
    "execute_list",
    "execute_update",
    "execute_delete",
    "execute_bulk_create",
    "execute_bulk_update",
    "execute_bulk_delete",
]

# Map method name -> operation label for span names
_METHOD_TO_OPERATION = {
    "execute_create": "create",
    "execute_get": "get",
    "execute_list": "list",
    "execute_update": "update",
    "execute_delete": "delete",
    "execute_bulk_create": "bulk_create",
    "execute_bulk_update": "bulk_update",
    "execute_bulk_delete": "bulk_delete",
}


class SlipStreamInstrumentor:
    """Instruments slip-stream components with OpenTelemetry tracing.

    Wraps ``OperationExecutor`` CRUD methods and ``EventBus.emit`` with
    traced spans. All spans carry structured attributes for schema name,
    operation, entity ID, record version, and user ID.

    Args:
        tracer_provider: Optional ``TracerProvider``. When ``None``, the
            global provider is used (``trace.get_tracer_provider()``).
        service_name: Tracer / instrumentation scope name.

    Raises:
        ImportError: If ``opentelemetry-api`` is not installed.

    Usage::

        instrumentor = SlipStreamInstrumentor()
        instrumentor.instrument_operation_executor()
        instrumentor.instrument_event_bus(event_bus)

        # Later, to restore original behaviour:
        instrumentor.uninstrument()
    """

    def __init__(
        self,
        tracer_provider: Optional[Any] = None,
        service_name: str = "slip-stream",
    ) -> None:
        if not HAS_OTEL:
            raise ImportError(
                "opentelemetry-api is required for telemetry. "
                "Install it with: pip install slip-stream[telemetry]"
            )
        if tracer_provider is not None:
            self._tracer = tracer_provider.get_tracer(service_name)
        else:
            self._tracer = trace.get_tracer(service_name)

        # Track patched objects so uninstrument() can restore them
        self._patched_executor_methods: dict[str, Any] = {}
        self._patched_event_bus: Optional[Any] = None
        self._original_emit: Optional[Any] = None

    # ------------------------------------------------------------------
    # OperationExecutor instrumentation
    # ------------------------------------------------------------------

    def instrument_operation_executor(self) -> None:
        """Monkey-patch all ``OperationExecutor.execute_*`` class methods.

        Each method is wrapped in a span that records ``schema_name``,
        ``operation``, ``entity_id``, ``record_version``, and ``user_id``
        as span attributes.

        Calling this method multiple times is safe — already-patched methods
        are skipped.
        """
        from slip_stream.core.operation import OperationExecutor

        for method_name in _EXECUTOR_METHODS:
            if method_name in self._patched_executor_methods:
                # Already patched — skip to stay idempotent
                continue

            original = getattr(OperationExecutor, method_name)
            operation_label = _METHOD_TO_OPERATION[method_name]
            tracer = self._tracer

            # Build the traced wrapper capturing original + labels by value
            wrapped = _make_executor_wrapper(original, operation_label, tracer)
            setattr(OperationExecutor, method_name, wrapped)
            self._patched_executor_methods[method_name] = original
            logger.debug(
                "SlipStreamInstrumentor: patched OperationExecutor.%s", method_name
            )

    # ------------------------------------------------------------------
    # EventBus instrumentation
    # ------------------------------------------------------------------

    def instrument_event_bus(self, event_bus: Any) -> None:
        """Wrap ``event_bus.emit`` so each lifecycle hook emits a child span.

        The span is named ``slip_stream.hook.{event}`` and carries the
        ``schema_name`` attribute from the context.

        Args:
            event_bus: A ``slip_stream.core.events.EventBus`` instance.
        """
        if self._patched_event_bus is event_bus:
            return  # Already instrumented this instance

        original_emit = event_bus.emit
        tracer = self._tracer

        async def traced_emit(event: str, ctx: Any) -> None:
            span_name = f"slip_stream.hook.{event}"
            with tracer.start_as_current_span(span_name) as span:
                span.set_attribute("slip_stream.event", event)
                span.set_attribute(
                    "slip_stream.schema_name", getattr(ctx, "schema_name", "")
                )
                try:
                    await original_emit(event, ctx)
                except Exception as exc:
                    span.set_status(Status(StatusCode.ERROR, str(exc)))
                    span.record_exception(exc)
                    raise

        event_bus.emit = traced_emit
        self._patched_event_bus = event_bus
        self._original_emit = original_emit
        logger.debug("SlipStreamInstrumentor: patched EventBus.emit")

    # ------------------------------------------------------------------
    # Uninstrument
    # ------------------------------------------------------------------

    def uninstrument(self) -> None:
        """Restore all patched methods to their originals.

        Safe to call even if ``instrument_*`` was never called.
        """
        from slip_stream.core.operation import OperationExecutor

        for method_name, original in self._patched_executor_methods.items():
            setattr(OperationExecutor, method_name, original)
            logger.debug(
                "SlipStreamInstrumentor: restored OperationExecutor.%s", method_name
            )
        self._patched_executor_methods.clear()

        if self._patched_event_bus is not None:
            # Remove the instance-level attribute so the class method is used again
            try:
                del self._patched_event_bus.emit
            except AttributeError:
                pass
            logger.debug("SlipStreamInstrumentor: restored EventBus.emit")
            self._patched_event_bus = None
            self._original_emit = None


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _make_executor_wrapper(original: Any, operation_label: str, tracer: Any) -> Any:
    """Return an async traced wrapper for an OperationExecutor method.

    Keeps a clean closure over ``original``, ``operation_label``, and
    ``tracer`` so the wrapper behaves correctly even after multiple
    ``instrument_operation_executor()`` calls.

    Args:
        original: The unpatched coroutine method.
        operation_label: Short operation name for the span (e.g. ``"create"``).
        tracer: The OTel ``Tracer`` instance.

    Returns:
        An async function with the same signature as the original.
    """

    async def traced_method(self_executor: Any, ctx: Any) -> Any:
        schema_name = getattr(ctx, "schema_name", "unknown")
        span_name = f"slip_stream.{schema_name}.{operation_label}"

        with tracer.start_as_current_span(span_name) as span:
            span.set_attribute("slip_stream.schema_name", schema_name)
            span.set_attribute("slip_stream.operation", operation_label)

            entity_id = getattr(ctx, "entity_id", None)
            if entity_id is not None:
                span.set_attribute("slip_stream.entity_id", str(entity_id))

            user_id = None
            current_user = getattr(ctx, "current_user", None)
            if current_user is not None:
                user_id = (
                    current_user.get("id")
                    if hasattr(current_user, "get")
                    else getattr(current_user, "id", None)
                )
            if user_id is not None:
                span.set_attribute("slip_stream.user_id", str(user_id))

            try:
                result = await original(self_executor, ctx)
            except Exception as exc:
                span.set_status(Status(StatusCode.ERROR, str(exc)))
                span.record_exception(exc)
                raise

            # Capture record_version from the result after execution
            record_version = getattr(
                getattr(ctx, "result", None), "record_version", None
            )
            if record_version is not None:
                span.set_attribute("slip_stream.record_version", int(record_version))

            return result

    traced_method.__name__ = original.__name__
    traced_method.__qualname__ = original.__qualname__
    return traced_method
