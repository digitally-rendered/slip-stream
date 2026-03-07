"""InMemory stream adapter benchmarks for slip-stream.

Run with:
    poetry run pytest benchmarks/stream/ --benchmark-only -v
    poetry run pytest benchmarks/stream/ --benchmark-json=benchmarks/results/stream-bench.json
"""

import asyncio

import pytest

from slip_stream.adapters.streaming.base import (
    EventStreamBridge,
    InMemoryStream,
)


@pytest.fixture
def stream():
    return InMemoryStream()


@pytest.fixture
def event_loop_runner():
    """Provide a simple async runner for benchmarks."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


def test_bench_inmemory_publish(benchmark, stream, event_loop_runner):
    """Benchmark single event publish latency."""

    async def _publish():
        await stream.publish(
            topic="slip-stream.pet.create",
            key="entity-1",
            payload={"event": "create", "schema_name": "pet"},
        )

    def run():
        event_loop_runner.run_until_complete(_publish())

    benchmark(run)
    assert len(stream.events) > 0


def test_bench_inmemory_fanout(benchmark, event_loop_runner):
    """Benchmark publish to 3 adapters simultaneously."""
    adapters = [InMemoryStream() for _ in range(3)]

    async def _fanout():
        for adapter in adapters:
            await adapter.publish(
                topic="slip-stream.pet.create",
                key="entity-1",
                payload={"event": "create"},
            )

    def run():
        event_loop_runner.run_until_complete(_fanout())

    benchmark(run)
    for a in adapters:
        assert len(a.events) > 0


def test_bench_bridge_roundtrip(benchmark, event_loop_runner):
    """Benchmark EventStreamBridge end-to-end: register → emit → publish."""

    class FakeEventBus:
        def __init__(self):
            self._handlers = {}

        def register(self, event_type, handler):
            self._handlers.setdefault(event_type, []).append(handler)

        async def emit(self, event_type, ctx):
            for h in self._handlers.get(event_type, []):
                await h(ctx)

    class FakeCtx:
        def __init__(self):
            self.schema_name = "pet"
            self.entity_id = "entity-123"
            self.current_user = {"id": "bench"}
            self.channel = "rest"
            self.data = {"name": "Fido", "status": "available"}
            self.result = None

    stream = InMemoryStream()
    bus = FakeEventBus()
    bridge = EventStreamBridge(adapters=[stream])
    bridge.register(bus)
    ctx = FakeCtx()

    async def _roundtrip():
        await bus.emit("post_create", ctx)

    def run():
        event_loop_runner.run_until_complete(_roundtrip())

    benchmark(run)
    assert len(stream.events) > 0


def test_bench_publish_throughput(benchmark, event_loop_runner):
    """Benchmark sustained throughput: 1000 events."""
    stream = InMemoryStream()

    async def _burst():
        for i in range(1000):
            await stream.publish(
                topic="slip-stream.pet.create",
                key=f"entity-{i}",
                payload={"event": "create", "n": i},
            )

    def run():
        event_loop_runner.run_until_complete(_burst())

    benchmark(run)
    assert len(stream.events) >= 1000


def test_bench_publish_throughput_5k(benchmark, event_loop_runner):
    """Benchmark sustained throughput: 5000 events."""
    stream = InMemoryStream()

    async def _burst():
        for i in range(5000):
            await stream.publish(
                topic="slip-stream.pet.create",
                key=f"entity-{i}",
                payload={"event": "create", "n": i},
            )

    def run():
        event_loop_runner.run_until_complete(_burst())

    benchmark(run)
    assert len(stream.events) >= 5000
