/**
 * k6 Performance Baseline — CRUD operations across all petstore entities.
 *
 * Usage:
 *   k6 run benchmarks/k6/perf_baseline.js                          # defaults (smoke)
 *   k6 run benchmarks/k6/perf_baseline.js --env BASE_URL=http://localhost:8200/api/v1
 *   k6 run benchmarks/k6/perf_baseline.js --env SCENARIO=load
 *   k6 run benchmarks/k6/perf_baseline.js --env SCENARIO=stress
 *   k6 run benchmarks/k6/perf_baseline.js --env SCENARIO=breaking  # ramp to failure
 *   k6 run benchmarks/k6/perf_baseline.js --env ENTITIES=pet,order  # subset
 *   k6 run benchmarks/k6/perf_baseline.js --env SCHEMA_DIR=./my-schemas
 *
 * Environment variables:
 *   BASE_URL    — API base URL (default: http://localhost:8100/api/v1)
 *   SCENARIO    — smoke | load | stress | breaking (default: smoke)
 *   ENTITIES    — comma-separated entity names (default: pet,order,user,tag,category)
 *   SCHEMA_DIR  — path to JSON schema directory for dynamic entity discovery (optional)
 *   PAYLOAD_FILE — path to custom payloads JSON (default: benchmarks/k6/data/payloads.json)
 *
 * Output:
 *   k6 outputs standard metrics: http_req_duration (p50/p95/p99), http_reqs, iterations, etc.
 *   Custom trends per entity and operation are tagged for filtering.
 */

import http from "k6/http";
import { check, group, sleep } from "k6";
import { Counter, Trend } from "k6/metrics";

// ---------------------------------------------------------------------------
// Configuration
// ---------------------------------------------------------------------------

const BASE_URL = __ENV.BASE_URL || "http://localhost:8100/api/v1";
const SCENARIO = __ENV.SCENARIO || "smoke";
const ENTITIES = (__ENV.ENTITIES || "pet,order,user,tag,category").split(",");

// Default payloads — override with PAYLOAD_FILE env var
const DEFAULT_PAYLOADS = {
  pet: {
    create: {
      name: "Buddy",
      status: "available",
      category: "Dogs",
      tags: ["friendly", "trained"],
      photo_urls: ["https://example.com/buddy.jpg"],
    },
    update: { status: "sold" },
  },
  order: {
    create: { pet_id: "placeholder", quantity: 1, status: "placed", complete: false },
    update: { status: "approved" },
  },
  user: {
    create: {
      username: "benchuser",
      email: "bench@example.com",
      first_name: "Bench",
      last_name: "User",
      phone: "555-0100",
      user_status: 1,
    },
    update: { user_status: 0 },
  },
  tag: {
    create: { name: "benchmark-tag" },
    update: { name: "benchmark-tag-updated" },
  },
  category: {
    create: { name: "benchmark-category" },
    update: { name: "benchmark-category-updated" },
  },
};

let PAYLOADS = DEFAULT_PAYLOADS;
if (__ENV.PAYLOAD_FILE) {
  PAYLOADS = JSON.parse(open(__ENV.PAYLOAD_FILE));
}

// ---------------------------------------------------------------------------
// Scenarios
// ---------------------------------------------------------------------------

const SCENARIOS = {
  smoke: {
    executor: "per-vu-iterations",
    vus: 1,
    iterations: 5,
    maxDuration: "30s",
  },
  load: {
    executor: "constant-vus",
    vus: 10,
    duration: "30s",
  },
  stress: {
    executor: "constant-vus",
    vus: 50,
    duration: "60s",
  },
  breaking: {
    // Ramp up VUs until the app breaks — identifies failure points
    executor: "ramping-vus",
    startVUs: 1,
    stages: [
      { duration: "30s", target: 20 },
      { duration: "30s", target: 50 },
      { duration: "30s", target: 100 },
      { duration: "30s", target: 200 },
      { duration: "30s", target: 500 },
      { duration: "30s", target: 0 },   // ramp down
    ],
    gracefulRampDown: "10s",
  },
};

export const options = {
  scenarios: {
    default: SCENARIOS[SCENARIO] || SCENARIOS.smoke,
  },
  thresholds: SCENARIO === "breaking"
    ? {
        // Breaking test: no strict thresholds — we want to observe where it fails
        http_req_duration: [{ threshold: "p(95)<5000", abortOnFail: false }],
        http_req_failed: [{ threshold: "rate<0.50", abortOnFail: false }],
      }
    : {
        http_req_duration: ["p(95)<500"],
        http_req_failed: ["rate<0.01"],
      },
};

// ---------------------------------------------------------------------------
// Custom metrics per entity × operation
// ---------------------------------------------------------------------------

const createTrend = new Trend("create_duration", true);
const getTrend = new Trend("get_duration", true);
const listTrend = new Trend("list_duration", true);
const updateTrend = new Trend("update_duration", true);
const deleteTrend = new Trend("delete_duration", true);
const errorCounter = new Counter("crud_errors");

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const HEADERS = { "Content-Type": "application/json" };

function uniquePayload(entity, vu, iter) {
  const base = JSON.parse(JSON.stringify(PAYLOADS[entity]?.create || { name: `${entity}-${vu}-${iter}` }));
  // Make values unique to avoid collisions under concurrency
  const suffix = `${vu}-${iter}-${Date.now()}`;
  if (base.name !== undefined) base.name = `${base.name}-${suffix}`;
  if (base.username !== undefined) base.username = `${base.username}-${suffix}`;
  if (base.email !== undefined) base.email = `bench-${suffix}@example.com`;
  return base;
}

function entityUrl(entity) {
  return `${BASE_URL}/${entity.replace(/_/g, "-")}`;
}

// ---------------------------------------------------------------------------
// Main test function — full CRUD cycle per entity
// ---------------------------------------------------------------------------

export default function () {
  const vu = __VU;
  const iter = __ITER;

  for (const entity of ENTITIES) {
    const url = entityUrl(entity);
    const payload = uniquePayload(entity, vu, iter);
    const updateData = PAYLOADS[entity]?.update || {};
    const tags = { entity: entity };

    group(`${entity} CRUD`, () => {
      // --- CREATE ---
      const createRes = http.post(`${url}/`, JSON.stringify(payload), {
        headers: HEADERS,
        tags: tags,
      });
      createTrend.add(createRes.timings.duration, tags);

      const createOk = check(createRes, {
        "create status 200 or 201": (r) => r.status === 200 || r.status === 201,
        "create has entity_id": (r) => {
          try {
            const body = r.json();
            // Handle envelope (slip-stream) or flat (stellar-drive) responses
            const data = body.data || body;
            return data.entity_id !== undefined && data.entity_id !== null;
          } catch (e) {
            return false;
          }
        },
      });

      if (!createOk) {
        errorCounter.add(1, tags);
        return;
      }

      const createBody = createRes.json();
      const created = createBody.data || createBody;
      const entityId = created.entity_id;

      // --- GET by ID ---
      const getRes = http.get(`${url}/${entityId}`, { tags: tags });
      getTrend.add(getRes.timings.duration, tags);

      check(getRes, {
        "get status 200": (r) => r.status === 200,
        "get correct entity_id": (r) => {
          try {
            const body = r.json();
            const data = body.data || body;
            return data.entity_id === entityId;
          } catch (e) {
            return false;
          }
        },
      });

      // --- LIST ---
      const listRes = http.get(`${url}/`, { tags: tags });
      listTrend.add(listRes.timings.duration, tags);

      check(listRes, {
        "list status 200": (r) => r.status === 200,
        "list returns array or data array": (r) => {
          try {
            const body = r.json();
            return Array.isArray(body) || Array.isArray(body.data);
          } catch (e) {
            return false;
          }
        },
      });

      // --- UPDATE ---
      const updateRes = http.patch(
        `${url}/${entityId}`,
        JSON.stringify(updateData),
        { headers: HEADERS, tags: tags }
      );
      updateTrend.add(updateRes.timings.duration, tags);

      check(updateRes, {
        "update status 200": (r) => r.status === 200,
        "update increments record_version": (r) => {
          try {
            const body = r.json();
            const data = body.data || body;
            return data.record_version === 2;
          } catch (e) {
            return false;
          }
        },
      });

      // --- DELETE ---
      const deleteRes = http.del(`${url}/${entityId}`, null, { tags: tags });
      deleteTrend.add(deleteRes.timings.duration, tags);

      check(deleteRes, {
        "delete status 200 or 204": (r) => r.status === 200 || r.status === 204,
      });

      // --- Verify soft-delete (GET should return 404) ---
      const getAfterDelete = http.get(`${url}/${entityId}`, { tags: tags });
      check(getAfterDelete, {
        "get after delete returns 404": (r) => r.status === 404,
      });
    });

    sleep(0.1); // Brief pause between entities
  }
}

// ---------------------------------------------------------------------------
// Summary handler — output structured results
// ---------------------------------------------------------------------------

export function handleSummary(data) {
  const summary = {
    timestamp: new Date().toISOString(),
    base_url: BASE_URL,
    scenario: SCENARIO,
    entities: ENTITIES,
    metrics: {
      http_req_duration_p50: data.metrics.http_req_duration?.values?.["p(50)"],
      http_req_duration_p95: data.metrics.http_req_duration?.values?.["p(95)"],
      http_req_duration_p99: data.metrics.http_req_duration?.values?.["p(99)"],
      http_req_duration_avg: data.metrics.http_req_duration?.values?.avg,
      http_reqs_total: data.metrics.http_reqs?.values?.count,
      http_reqs_rate: data.metrics.http_reqs?.values?.rate,
      http_req_failed_rate: data.metrics.http_req_failed?.values?.rate,
      create_p95: data.metrics.create_duration?.values?.["p(95)"],
      get_p95: data.metrics.get_duration?.values?.["p(95)"],
      list_p95: data.metrics.list_duration?.values?.["p(95)"],
      update_p95: data.metrics.update_duration?.values?.["p(95)"],
      delete_p95: data.metrics.delete_duration?.values?.["p(95)"],
      crud_errors: data.metrics.crud_errors?.values?.count || 0,
    },
  };

  return {
    stdout: JSON.stringify(summary, null, 2) + "\n",
    "benchmarks/results/k6-summary.json": JSON.stringify(summary, null, 2),
  };
}
