# ---- Builder stage ----
FROM python:3.13-slim AS builder

RUN pip install --no-cache-dir poetry

WORKDIR /build

COPY pyproject.toml poetry.lock README.md ./
COPY slip_stream/ slip_stream/

RUN poetry build -f wheel

# ---- Runtime stage ----
FROM python:3.13-slim

LABEL maintainer="digitally-rendered"
LABEL description="slip-stream MCP server — JSON Schema-driven hexagonal backend framework"
LABEL org.opencontainers.image.source="https://github.com/digitally-rendered/slip-stream"

WORKDIR /app

COPY --from=builder /build/dist/*.whl /tmp/

RUN pip install --no-cache-dir "$(ls /tmp/*.whl)[mcp,remote]" && \
    rm -rf /tmp/*.whl

VOLUME /schemas

ENTRYPOINT ["python", "-m", "slip_stream.mcp.server"]
CMD ["--schema-dir", "/schemas"]
