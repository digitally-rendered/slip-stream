FROM python:3.12-slim AS base

WORKDIR /app

RUN pip install --no-cache-dir poetry && \
    poetry config virtualenvs.create false

COPY pyproject.toml poetry.lock* ./
RUN poetry install --no-interaction --no-ansi --only main --extras yaml

COPY slip_stream/ slip_stream/
COPY benchmarks/ benchmarks/

ENV BENCH_PORT=8100
ENV BENCH_MONGO_URI=mongodb://mongo:27017
ENV BENCH_DB_NAME=slip_stream_bench

EXPOSE 8100

HEALTHCHECK --interval=5s --timeout=3s --retries=10 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:${BENCH_PORT}/health')"

CMD ["python", "-m", "uvicorn", "benchmarks.app:app", "--host", "0.0.0.0", "--port", "8100"]
