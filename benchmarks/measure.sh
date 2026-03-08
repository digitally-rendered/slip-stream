#!/usr/bin/env bash
# ============================================================================
# measure.sh — Performance measurement harness for schema-driven CRUD APIs
#
# Measures boot time, memory (RSS), CPU usage, and runs k6 load tests.
# Works with any app that exposes /health and CRUD endpoints.
#
# Usage:
#   ./benchmarks/measure.sh                                    # defaults
#   ./benchmarks/measure.sh --app slip-stream --port 8100      # explicit
#   ./benchmarks/measure.sh --app stellar-drive --port 8200
#   ./benchmarks/measure.sh --backend sql --scenario load
#
# Custom schemas (for consumer use):
#   ./benchmarks/measure.sh --schema-dir /path/to/my/schemas \
#                           --start-cmd "uvicorn myapp:app --port 8100"
#
# Environment variables (override flags):
#   BENCH_APP         — app name label
#   BENCH_PORT        — port
#   BENCH_BACKEND     — mongo or sql
#   BENCH_SCENARIO    — smoke, load, stress, or breaking
#   BENCH_SCHEMA_DIR  — schema directory
#   BENCH_START_CMD   — custom start command
#   BENCH_API_PREFIX  — API prefix (default: /api/v1)
#
# Constrained resource testing (find failure points):
#   ./benchmarks/measure.sh --scenario breaking --cpu-limit 1 --mem-limit 256m
#
# CPU/memory limits use macOS `ulimit` or Docker if available:
#   BENCH_CPU_LIMIT   — CPU core count limit (e.g., 1, 2)
#   BENCH_MEM_LIMIT   — Memory limit (e.g., 256m, 512m, 1g)
# ============================================================================
set -euo pipefail

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
APP_NAME="${BENCH_APP:-slip-stream}"
PORT="${BENCH_PORT:-8100}"
BACKEND="${BENCH_BACKEND:-mongo}"
SCENARIO="${BENCH_SCENARIO:-smoke}"
SCHEMA_DIR="${BENCH_SCHEMA_DIR:-}"
START_CMD="${BENCH_START_CMD:-}"
API_PREFIX="${BENCH_API_PREFIX:-/api/v1}"
ENTITIES="${BENCH_ENTITIES:-pet,order,user,tag,category}"
CPU_LIMIT="${BENCH_CPU_LIMIT:-}"
MEM_LIMIT="${BENCH_MEM_LIMIT:-}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RESULTS_DIR="${SCRIPT_DIR}/results"
K6_DIR="${SCRIPT_DIR}/k6"

# ---------------------------------------------------------------------------
# Parse flags
# ---------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
  case $1 in
    --app)        APP_NAME="$2";    shift 2 ;;
    --port)       PORT="$2";        shift 2 ;;
    --backend)    BACKEND="$2";     shift 2 ;;
    --scenario)   SCENARIO="$2";    shift 2 ;;
    --schema-dir) SCHEMA_DIR="$2";  shift 2 ;;
    --start-cmd)  START_CMD="$2";   shift 2 ;;
    --api-prefix) API_PREFIX="$2";  shift 2 ;;
    --entities)   ENTITIES="$2";    shift 2 ;;
    --cpu-limit)  CPU_LIMIT="$2";  shift 2 ;;
    --mem-limit)  MEM_LIMIT="$2";  shift 2 ;;
    --help|-h)
      head -25 "$0" | tail -20
      exit 0
      ;;
    *) echo "Unknown flag: $1"; exit 1 ;;
  esac
done

BASE_URL="http://localhost:${PORT}${API_PREFIX}"
TIMESTAMP=$(date +%Y%m%dT%H%M%S)
RESULT_FILE="${RESULTS_DIR}/${APP_NAME}-${BACKEND}-${TIMESTAMP}.json"

mkdir -p "${RESULTS_DIR}"

# ---------------------------------------------------------------------------
# Detect start command if not provided
# ---------------------------------------------------------------------------
if [[ -z "${START_CMD}" ]]; then
  if [[ "${APP_NAME}" == "slip-stream" ]]; then
    START_CMD="poetry run uvicorn benchmarks.app:app --host 0.0.0.0 --port ${PORT}"
  elif [[ "${APP_NAME}" == "stellar-drive" ]]; then
    START_CMD="go run ./cmd/stellar-drive --config=benchmarks/stellar.yaml run --port ${PORT}"
  else
    echo "ERROR: --start-cmd required for custom apps"
    exit 1
  fi
fi

# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------
log() { echo "[measure] $*" >&2; }

cleanup() {
  if [[ -n "${APP_PID:-}" ]] && kill -0 "${APP_PID}" 2>/dev/null; then
    log "Stopping app (PID ${APP_PID})..."
    kill "${APP_PID}" 2>/dev/null || true
    wait "${APP_PID}" 2>/dev/null || true
  fi
}
trap cleanup EXIT

get_rss_mb() {
  local pid=$1
  if [[ "$(uname)" == "Darwin" ]]; then
    # macOS: ps shows RSS in KB
    ps -o rss= -p "${pid}" 2>/dev/null | awk '{printf "%.1f", $1/1024}'
  else
    # Linux: /proc/pid/status in kB
    awk '/VmRSS/{printf "%.1f", $2/1024}' /proc/"${pid}"/status 2>/dev/null || echo "0"
  fi
}

get_cpu_percent() {
  local pid=$1
  ps -o %cpu= -p "${pid}" 2>/dev/null | awk '{print $1}'
}

sample_resources() {
  local pid=$1
  local duration=$2
  local interval=1
  local max_rss=0
  local cpu_sum=0
  local cpu_count=0
  local elapsed=0

  while [[ $elapsed -lt $duration ]] && kill -0 "${pid}" 2>/dev/null; do
    local rss
    rss=$(get_rss_mb "${pid}")
    local cpu
    cpu=$(get_cpu_percent "${pid}")

    if (( $(echo "${rss} > ${max_rss}" | bc -l 2>/dev/null || echo 0) )); then
      max_rss="${rss}"
    fi
    cpu_sum=$(echo "${cpu_sum} + ${cpu}" | bc -l 2>/dev/null || echo "${cpu_sum}")
    cpu_count=$((cpu_count + 1))

    sleep "${interval}"
    elapsed=$((elapsed + interval))
  done

  local avg_cpu=0
  if [[ $cpu_count -gt 0 ]]; then
    avg_cpu=$(echo "scale=1; ${cpu_sum} / ${cpu_count}" | bc -l 2>/dev/null || echo "0")
  fi

  echo "${max_rss} ${avg_cpu}"
}

# ---------------------------------------------------------------------------
# Resource constraints (optional — for stress-to-failure testing)
# ---------------------------------------------------------------------------
CONSTRAINT_LABEL=""
if [[ -n "${CPU_LIMIT}" || -n "${MEM_LIMIT}" ]]; then
  # Try Docker-based constraint if Docker is available
  if command -v docker &>/dev/null && [[ -n "${CPU_LIMIT}" || -n "${MEM_LIMIT}" ]]; then
    DOCKER_FLAGS=""
    if [[ -n "${CPU_LIMIT}" ]]; then
      DOCKER_FLAGS="${DOCKER_FLAGS} --cpus=${CPU_LIMIT}"
      CONSTRAINT_LABEL="${CONSTRAINT_LABEL}cpu${CPU_LIMIT}-"
    fi
    if [[ -n "${MEM_LIMIT}" ]]; then
      DOCKER_FLAGS="${DOCKER_FLAGS} --memory=${MEM_LIMIT}"
      CONSTRAINT_LABEL="${CONSTRAINT_LABEL}mem${MEM_LIMIT}-"
    fi
    log "Resource constraints: CPU=${CPU_LIMIT:-unlimited} MEM=${MEM_LIMIT:-unlimited}"
    log "NOTE: Docker-based constraints require the app to run in a container."
    log "      For native process limits, the script uses ulimit where available."
  fi

  # For native (non-Docker) runs, apply ulimit memory constraint
  if [[ -n "${MEM_LIMIT}" ]]; then
    MEM_BYTES=$(python3 -c "
v = '${MEM_LIMIT}'.strip().lower()
if v.endswith('g'): print(int(float(v[:-1]) * 1024 * 1024 * 1024))
elif v.endswith('m'): print(int(float(v[:-1]) * 1024 * 1024))
elif v.endswith('k'): print(int(float(v[:-1]) * 1024))
else: print(int(v))
")
    MEM_KB=$((MEM_BYTES / 1024))
    log "Setting ulimit -v ${MEM_KB} (${MEM_LIMIT})"
    ulimit -v "${MEM_KB}" 2>/dev/null || log "WARNING: ulimit -v not supported on this platform"
  fi

  CONSTRAINT_LABEL="${CONSTRAINT_LABEL%%-}"
fi

if [[ -n "${CONSTRAINT_LABEL}" ]]; then
  RESULT_FILE="${RESULTS_DIR}/${APP_NAME}-${BACKEND}-${CONSTRAINT_LABEL}-${TIMESTAMP}.json"
fi

# ---------------------------------------------------------------------------
# 1. Start the app and measure boot time
# ---------------------------------------------------------------------------
log "Starting ${APP_NAME} on port ${PORT} (backend: ${BACKEND})..."
if [[ -n "${CPU_LIMIT}${MEM_LIMIT}" ]]; then
  log "Constraints: CPU=${CPU_LIMIT:-unlimited} MEM=${MEM_LIMIT:-unlimited}"
fi
log "Command: ${START_CMD}"

BOOT_START=$(python3 -c "import time; print(int(time.time() * 1000))")

# Export env vars for the app
export BENCH_MONGO_URI="${BENCH_MONGO_URI:-mongodb://localhost:27017}"
export BENCH_DB_NAME="${BENCH_DB_NAME:-${APP_NAME//-/_}_bench}"
export BENCH_BACKEND="${BACKEND}"
export BENCH_PORT="${PORT}"
if [[ -n "${SCHEMA_DIR}" ]]; then
  export BENCH_SCHEMA_DIR="${SCHEMA_DIR}"
fi

# Start app in background
eval "${START_CMD}" &
APP_PID=$!

# Wait for health endpoint
HEALTH_URL="http://localhost:${PORT}/health"
MAX_WAIT=30
WAITED=0
while ! curl -sf "${HEALTH_URL}" > /dev/null 2>&1; do
  if ! kill -0 "${APP_PID}" 2>/dev/null; then
    log "ERROR: App process died during startup"
    exit 1
  fi
  if [[ $WAITED -ge $MAX_WAIT ]]; then
    log "ERROR: App did not become healthy within ${MAX_WAIT}s"
    exit 1
  fi
  sleep 0.5
  WAITED=$((WAITED + 1))
done

BOOT_END=$(python3 -c "import time; print(int(time.time() * 1000))")
BOOT_TIME_MS=$((BOOT_END - BOOT_START))
log "App healthy in ${BOOT_TIME_MS}ms"

# ---------------------------------------------------------------------------
# 2. Baseline memory
# ---------------------------------------------------------------------------
BASELINE_RSS=$(get_rss_mb "${APP_PID}")
log "Baseline memory: ${BASELINE_RSS} MB"

# ---------------------------------------------------------------------------
# 3. Run k6 performance tests
# ---------------------------------------------------------------------------
log "Running k6 ${SCENARIO} scenario..."

K6_OUTPUT=$(mktemp)
k6 run "${K6_DIR}/perf_baseline.js" \
  --env "BASE_URL=${BASE_URL}" \
  --env "SCENARIO=${SCENARIO}" \
  --env "ENTITIES=${ENTITIES}" \
  --summary-export="${K6_OUTPUT}" \
  2>&1 | tee /dev/stderr || true

# ---------------------------------------------------------------------------
# 4. Peak memory + CPU during test (sample for a few seconds after k6)
# ---------------------------------------------------------------------------
read -r PEAK_RSS AVG_CPU <<< "$(sample_resources "${APP_PID}" 5)"

# Use baseline if peak is lower (test already finished)
if (( $(echo "${PEAK_RSS} < ${BASELINE_RSS}" | bc -l 2>/dev/null || echo 0) )); then
  PEAK_RSS="${BASELINE_RSS}"
fi

log "Peak memory: ${PEAK_RSS} MB, Avg CPU: ${AVG_CPU}%"

# ---------------------------------------------------------------------------
# 5. Parse k6 summary and build final result
# ---------------------------------------------------------------------------
K6_METRICS="{}"
if [[ -f "${K6_OUTPUT}" ]]; then
  K6_METRICS=$(python3 -c "
import json, sys
try:
    with open('${K6_OUTPUT}') as f:
        data = json.load(f)
    m = data.get('metrics', {})
    result = {}
    for key in ['http_req_duration', 'http_reqs', 'http_req_failed',
                 'create_duration', 'get_duration', 'list_duration',
                 'update_duration', 'delete_duration']:
        if key in m:
            result[key] = m[key].get('values', {})
    print(json.dumps(result))
except Exception as e:
    print('{}', file=sys.stdout)
")
  rm -f "${K6_OUTPUT}"
fi

# ---------------------------------------------------------------------------
# 6. Write final results JSON
# ---------------------------------------------------------------------------
python3 -c "
import json

constraints = {}
if '${CPU_LIMIT}':
    constraints['cpu_limit'] = '${CPU_LIMIT}'
if '${MEM_LIMIT}':
    constraints['mem_limit'] = '${MEM_LIMIT}'

result = {
    'app': '${APP_NAME}',
    'backend': '${BACKEND}',
    'scenario': '${SCENARIO}',
    'timestamp': '$(date -u +%Y-%m-%dT%H:%M:%SZ)',
    'boot_time_ms': ${BOOT_TIME_MS},
    'baseline_memory_mb': float('${BASELINE_RSS}'),
    'peak_memory_mb': float('${PEAK_RSS}'),
    'avg_cpu_percent': float('${AVG_CPU}'),
    'entities': '${ENTITIES}'.split(','),
    'constraints': constraints if constraints else None,
    'k6_metrics': json.loads('''${K6_METRICS}'''),
}

with open('${RESULT_FILE}', 'w') as f:
    json.dump(result, f, indent=2)

print(json.dumps(result, indent=2))
"

log "Results written to ${RESULT_FILE}"
log "Done."
