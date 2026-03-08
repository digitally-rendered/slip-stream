/**
 * k6 Compatibility Test — verifies both slip-stream and stellar-drive
 * produce equivalent API behavior for the same operations.
 *
 * Usage:
 *   # Start both apps first:
 *   #   slip-stream on :8100, stellar-drive on :8200
 *   k6 run benchmarks/k6/compat_test.js
 *   k6 run benchmarks/k6/compat_test.js --env ENTITIES=pet,order
 *
 * Environment variables:
 *   APP_A_URL    — First app base URL (default: http://localhost:8100/api/v1)
 *   APP_B_URL    — Second app base URL (default: http://localhost:8200/api/v1)
 *   APP_A_NAME   — Label for first app (default: slip-stream)
 *   APP_B_NAME   — Label for second app (default: stellar-drive)
 *   ENTITIES     — comma-separated entity names (default: pet,order,user,tag,category)
 */

import http from "k6/http";
import { check, group } from "k6";
import { Counter } from "k6/metrics";

const APP_A_URL = __ENV.APP_A_URL || "http://localhost:8100/api/v1";
const APP_B_URL = __ENV.APP_B_URL || "http://localhost:8200/api/v1";
const APP_A_NAME = __ENV.APP_A_NAME || "slip-stream";
const APP_B_NAME = __ENV.APP_B_NAME || "stellar-drive";
const ENTITIES = (__ENV.ENTITIES || "pet,order,user,tag,category").split(",");

const HEADERS = { "Content-Type": "application/json" };

const compatErrors = new Counter("compat_errors");

const PAYLOADS = {
  pet: {
    create: { name: "CompatPet", status: "available", category: "Dogs", tags: ["test"], photo_urls: [] },
    update: { status: "pending" },
    invalid: { status: "INVALID_ENUM_VALUE" },  // missing required 'name'
  },
  order: {
    create: { pet_id: "compat-id", quantity: 2, status: "placed", complete: false },
    update: { status: "approved" },
    invalid: { quantity: -1 },  // missing required 'pet_id'
  },
  user: {
    create: { username: "compatuser", email: "compat@test.com", first_name: "A", last_name: "B", user_status: 1 },
    update: { user_status: 0 },
    invalid: { user_status: 1 },  // missing required 'username' and 'email'
  },
  tag: {
    create: { name: "compat-tag" },
    update: { name: "compat-tag-v2" },
    invalid: {},  // missing required 'name'
  },
  category: {
    create: { name: "compat-category" },
    update: { name: "compat-category-v2" },
    invalid: {},  // missing required 'name'
  },
};

export const options = {
  scenarios: {
    default: {
      executor: "per-vu-iterations",
      vus: 1,
      iterations: 1,
      maxDuration: "120s",
    },
  },
  thresholds: {
    compat_errors: ["count<1"],
  },
};

function entityUrl(baseUrl, entity) {
  return `${baseUrl}/${entity.replace(/_/g, "-")}`;
}

function extractData(response) {
  try {
    const body = response.json();
    return body.data || body;
  } catch (e) {
    return null;
  }
}

function extractList(response) {
  try {
    const body = response.json();
    if (Array.isArray(body)) return body;
    if (Array.isArray(body.data)) return body.data;
    return null;
  } catch (e) {
    return null;
  }
}

export default function () {
  const suffix = `${Date.now()}`;

  for (const entity of ENTITIES) {
    const urlA = entityUrl(APP_A_URL, entity);
    const urlB = entityUrl(APP_B_URL, entity);
    const payload = JSON.parse(JSON.stringify(PAYLOADS[entity].create));

    // Make payloads unique
    if (payload.name !== undefined) payload.name = `${payload.name}-${suffix}`;
    if (payload.username !== undefined) payload.username = `${payload.username}-${suffix}`;
    if (payload.email !== undefined) payload.email = `compat-${suffix}@test.com`;

    group(`${entity} compatibility`, () => {

      // ---- 1. CREATE on both ----
      group("create", () => {
        const resA = http.post(`${urlA}/`, JSON.stringify(payload), { headers: HEADERS });
        const resB = http.post(`${urlB}/`, JSON.stringify(payload), { headers: HEADERS });

        const ok = check(null, {
          "both return success (200/201)": () =>
            (resA.status === 200 || resA.status === 201) &&
            (resB.status === 200 || resB.status === 201),
          "both have entity_id": () => {
            const dA = extractData(resA);
            const dB = extractData(resB);
            return dA?.entity_id && dB?.entity_id;
          },
          "both have record_version=1": () => {
            const dA = extractData(resA);
            const dB = extractData(resB);
            return dA?.record_version === 1 && dB?.record_version === 1;
          },
          "both have created_at timestamp": () => {
            const dA = extractData(resA);
            const dB = extractData(resB);
            return dA?.created_at && dB?.created_at;
          },
        });
        if (!ok) compatErrors.add(1);

        // Store entity IDs for subsequent ops
        const dataA = extractData(resA);
        const dataB = extractData(resB);
        if (!dataA?.entity_id || !dataB?.entity_id) return;

        // ---- 2. GET by ID ----
        group("get", () => {
          const getA = http.get(`${urlA}/${dataA.entity_id}`);
          const getB = http.get(`${urlB}/${dataB.entity_id}`);

          const getOk = check(null, {
            "both GET return 200": () => getA.status === 200 && getB.status === 200,
            "both return correct entity": () => {
              const gA = extractData(getA);
              const gB = extractData(getB);
              return gA?.entity_id === dataA.entity_id && gB?.entity_id === dataB.entity_id;
            },
          });
          if (!getOk) compatErrors.add(1);
        });

        // ---- 3. LIST ----
        group("list", () => {
          const listA = http.get(`${urlA}/`);
          const listB = http.get(`${urlB}/`);

          const listOk = check(null, {
            "both LIST return 200": () => listA.status === 200 && listB.status === 200,
            "both return arrays": () => {
              const arrA = extractList(listA);
              const arrB = extractList(listB);
              return Array.isArray(arrA) && Array.isArray(arrB);
            },
          });
          if (!listOk) compatErrors.add(1);
        });

        // ---- 4. UPDATE ----
        group("update", () => {
          const updatePayload = JSON.stringify(PAYLOADS[entity].update);
          const updA = http.patch(`${urlA}/${dataA.entity_id}`, updatePayload, { headers: HEADERS });
          const updB = http.patch(`${urlB}/${dataB.entity_id}`, updatePayload, { headers: HEADERS });

          const updOk = check(null, {
            "both UPDATE return 200": () => updA.status === 200 && updB.status === 200,
            "both have record_version=2": () => {
              const uA = extractData(updA);
              const uB = extractData(updB);
              return uA?.record_version === 2 && uB?.record_version === 2;
            },
          });
          if (!updOk) compatErrors.add(1);
        });

        // ---- 5. DELETE ----
        group("delete", () => {
          const delA = http.del(`${urlA}/${dataA.entity_id}`);
          const delB = http.del(`${urlB}/${dataB.entity_id}`);

          const delOk = check(null, {
            "both DELETE return 200 or 204": () =>
              (delA.status === 200 || delA.status === 204) &&
              (delB.status === 200 || delB.status === 204),
          });
          if (!delOk) compatErrors.add(1);
        });

        // ---- 6. GET after DELETE → 404 ----
        group("get-after-delete", () => {
          const goneA = http.get(`${urlA}/${dataA.entity_id}`);
          const goneB = http.get(`${urlB}/${dataB.entity_id}`);

          const goneOk = check(null, {
            "both return 404 after delete": () => goneA.status === 404 && goneB.status === 404,
          });
          if (!goneOk) compatErrors.add(1);
        });

        // ---- 7. GET non-existent → 404 ----
        group("get-nonexistent", () => {
          const fakeId = "00000000-0000-0000-0000-000000000000";
          const nfA = http.get(`${urlA}/${fakeId}`);
          const nfB = http.get(`${urlB}/${fakeId}`);

          const nfOk = check(null, {
            "both return 404 for non-existent": () => nfA.status === 404 && nfB.status === 404,
          });
          if (!nfOk) compatErrors.add(1);
        });
      });
    });
  }
}

export function handleSummary(data) {
  const summary = {
    timestamp: new Date().toISOString(),
    app_a: { name: APP_A_NAME, url: APP_A_URL },
    app_b: { name: APP_B_NAME, url: APP_B_URL },
    entities: ENTITIES,
    compat_errors: data.metrics.compat_errors?.values?.count || 0,
    checks_passed: data.metrics.checks?.values?.passes || 0,
    checks_failed: data.metrics.checks?.values?.fails || 0,
    pass_rate: data.metrics.checks?.values?.rate || 0,
  };

  return {
    stdout: JSON.stringify(summary, null, 2) + "\n",
    "benchmarks/results/compat-summary.json": JSON.stringify(summary, null, 2),
  };
}
