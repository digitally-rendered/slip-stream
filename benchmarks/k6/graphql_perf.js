/**
 * k6 GraphQL Performance — CRUD lifecycle over GraphQL for all petstore entities.
 *
 * Usage:
 *   k6 run benchmarks/k6/graphql_perf.js
 *   k6 run benchmarks/k6/graphql_perf.js --env GQL_URL=http://localhost:8100/graphql
 *   k6 run benchmarks/k6/graphql_perf.js --env SCENARIO=load
 *   k6 run benchmarks/k6/graphql_perf.js --env SCENARIO=stress
 *
 * Environment variables:
 *   GQL_URL   — GraphQL endpoint (default: http://localhost:8100/graphql)
 *   SCENARIO  — smoke | load | stress (default: smoke)
 *
 * Custom metrics (all in milliseconds, true=isTime):
 *   graphql_create_duration
 *   graphql_get_duration
 *   graphql_list_duration
 *   graphql_update_duration
 *   graphql_delete_duration
 */

import http from "k6/http";
import { check, group, sleep } from "k6";
import { Counter, Trend } from "k6/metrics";

// ---------------------------------------------------------------------------
// Configuration
// ---------------------------------------------------------------------------

const GQL_URL = __ENV.GQL_URL || "http://localhost:8100/graphql";
const SCENARIO = __ENV.SCENARIO || "smoke";

// ---------------------------------------------------------------------------
// Custom metrics
// ---------------------------------------------------------------------------

const graphqlCreateDuration = new Trend("graphql_create_duration", true);
const graphqlGetDuration    = new Trend("graphql_get_duration",    true);
const graphqlListDuration   = new Trend("graphql_list_duration",   true);
const graphqlUpdateDuration = new Trend("graphql_update_duration", true);
const graphqlDeleteDuration = new Trend("graphql_delete_duration", true);
const graphqlErrors         = new Counter("graphql_errors");

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
    vus: 5,
    duration: "30s",
  },
  stress: {
    executor: "ramping-vus",
    startVUs: 1,
    stages: [
      { duration: "10s", target: 5  },
      { duration: "20s", target: 20 },
      { duration: "10s", target: 0  },
    ],
    gracefulRampDown: "5s",
  },
};

export const options = {
  scenarios: {
    default: SCENARIOS[SCENARIO] || SCENARIOS.smoke,
  },
  thresholds: SCENARIO === "stress"
    ? {
        http_req_duration: [{ threshold: "p(95)<2000", abortOnFail: false }],
        http_req_failed:   [{ threshold: "rate<0.10",  abortOnFail: false }],
      }
    : {
        http_req_duration: ["p(95)<500"],
        http_req_failed:   ["rate<0.01"],
      },
};

// ---------------------------------------------------------------------------
// GraphQL helpers
// ---------------------------------------------------------------------------

const GQL_HEADERS = {
  "Content-Type": "application/json",
  Accept: "application/json",
};

/**
 * Execute a GraphQL request and return the parsed response body.
 * Returns null on network error or non-200 status.
 */
function gql(query, variables) {
  const body = JSON.stringify({ query: query, variables: variables || {} });
  const res  = http.post(GQL_URL, body, { headers: GQL_HEADERS });
  if (res.status !== 200) {
    return { __status: res.status, __raw: res.body, timings: res.timings };
  }
  try {
    const parsed = res.json();
    parsed.__status  = res.status;
    parsed.__timings = res.timings;
    return parsed;
  } catch (_) {
    return { __status: res.status, __raw: res.body, timings: res.timings };
  }
}

/** Extract data[fieldName] from a GraphQL response, or null on errors. */
function extractData(resp, fieldName) {
  if (!resp || resp.__status !== 200) return null;
  if (resp.errors && resp.errors.length > 0) return null;
  return (resp.data || {})[fieldName] || null;
}

// ---------------------------------------------------------------------------
// GraphQL CRUD mutations / queries for 'pet'
// ---------------------------------------------------------------------------

const CREATE_PET = `
  mutation CreatePet($name: String!, $status: String!, $category: String) {
    createPet(input: { name: $name, status: $status, category: $category }) {
      entityId
      recordVersion
      schemaName
    }
  }
`;

const GET_PET = `
  query GetPet($entityId: String!) {
    getPet(entityId: $entityId) {
      entityId
      recordVersion
      schemaName
    }
  }
`;

const LIST_PETS = `
  query ListPets {
    listPets(limit: 10) {
      items {
        entityId
        recordVersion
      }
      total
    }
  }
`;

const UPDATE_PET = `
  mutation UpdatePet($entityId: String!, $status: String) {
    updatePet(entityId: $entityId, input: { status: $status }) {
      entityId
      recordVersion
    }
  }
`;

const DELETE_PET = `
  mutation DeletePet($entityId: String!) {
    deletePet(entityId: $entityId) {
      entityId
      recordVersion
    }
  }
`;

// ---------------------------------------------------------------------------
// Main test function
// ---------------------------------------------------------------------------

export default function () {
  const vu   = __VU;
  const iter = __ITER;
  const suffix = `${vu}-${iter}-${Date.now()}`;

  group("GraphQL pet CRUD lifecycle", () => {
    // --- CREATE ---
    const createResp = gql(CREATE_PET, {
      name:     `bench-pet-${suffix}`,
      status:   "available",
      category: "benchmark",
    });
    graphqlCreateDuration.add(
      (createResp && createResp.__timings) ? createResp.__timings.duration : 0
    );

    const createOk = check(createResp, {
      "graphql create: no errors": (r) =>
        r && (!r.errors || r.errors.length === 0),
      "graphql create: has entityId": (r) => {
        const d = extractData(r, "createPet");
        return d && d.entityId !== undefined && d.entityId !== null;
      },
    });

    if (!createOk) {
      graphqlErrors.add(1);
      return;
    }

    const entityId = extractData(createResp, "createPet").entityId;

    // --- GET ---
    const getResp = gql(GET_PET, { entityId: entityId });
    graphqlGetDuration.add(
      (getResp && getResp.__timings) ? getResp.__timings.duration : 0
    );

    check(getResp, {
      "graphql get: no errors": (r) =>
        r && (!r.errors || r.errors.length === 0),
      "graphql get: correct entityId": (r) => {
        const d = extractData(r, "getPet");
        return d && d.entityId === entityId;
      },
    });

    // --- LIST ---
    const listResp = gql(LIST_PETS, {});
    graphqlListDuration.add(
      (listResp && listResp.__timings) ? listResp.__timings.duration : 0
    );

    check(listResp, {
      "graphql list: no errors": (r) =>
        r && (!r.errors || r.errors.length === 0),
      "graphql list: returns items array": (r) => {
        const d = extractData(r, "listPets");
        return d && Array.isArray(d.items);
      },
    });

    // --- UPDATE ---
    const updateResp = gql(UPDATE_PET, { entityId: entityId, status: "sold" });
    graphqlUpdateDuration.add(
      (updateResp && updateResp.__timings) ? updateResp.__timings.duration : 0
    );

    check(updateResp, {
      "graphql update: no errors": (r) =>
        r && (!r.errors || r.errors.length === 0),
      "graphql update: record_version incremented": (r) => {
        const d = extractData(r, "updatePet");
        return d && d.recordVersion === 2;
      },
    });

    // --- DELETE ---
    const deleteResp = gql(DELETE_PET, { entityId: entityId });
    graphqlDeleteDuration.add(
      (deleteResp && deleteResp.__timings) ? deleteResp.__timings.duration : 0
    );

    check(deleteResp, {
      "graphql delete: no errors": (r) =>
        r && (!r.errors || r.errors.length === 0),
      "graphql delete: returns entityId": (r) => {
        const d = extractData(r, "deletePet");
        return d && d.entityId === entityId;
      },
    });
  });

  sleep(0.1);
}

// ---------------------------------------------------------------------------
// Summary handler
// ---------------------------------------------------------------------------

export function handleSummary(data) {
  const summary = {
    timestamp: new Date().toISOString(),
    gql_url:   GQL_URL,
    scenario:  SCENARIO,
    metrics: {
      http_req_duration_p50:     data.metrics.http_req_duration?.values?.["p(50)"],
      http_req_duration_p95:     data.metrics.http_req_duration?.values?.["p(95)"],
      http_req_duration_p99:     data.metrics.http_req_duration?.values?.["p(99)"],
      http_req_duration_avg:     data.metrics.http_req_duration?.values?.avg,
      http_reqs_total:           data.metrics.http_reqs?.values?.count,
      http_req_failed_rate:      data.metrics.http_req_failed?.values?.rate,
      graphql_create_p95:        data.metrics.graphql_create_duration?.values?.["p(95)"],
      graphql_get_p95:           data.metrics.graphql_get_duration?.values?.["p(95)"],
      graphql_list_p95:          data.metrics.graphql_list_duration?.values?.["p(95)"],
      graphql_update_p95:        data.metrics.graphql_update_duration?.values?.["p(95)"],
      graphql_delete_p95:        data.metrics.graphql_delete_duration?.values?.["p(95)"],
      graphql_errors:            data.metrics.graphql_errors?.values?.count || 0,
    },
  };

  return {
    stdout: JSON.stringify(summary, null, 2) + "\n",
    "benchmarks/results/k6-graphql-summary.json": JSON.stringify(summary, null, 2),
  };
}
