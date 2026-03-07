/**
 * Stream performance test for slip-stream and stellar-drive.
 *
 * Tests end-to-end event delivery by:
 * 1. Creating entities via the REST API
 * 2. Polling the event-consumer service to verify events arrived
 * 3. Measuring delivery latency and reliability
 *
 * Requires:
 *   - App running with streaming enabled (BENCH_STREAM=memory or a real broker)
 *   - event-consumer service running on CONSUMER_URL
 *
 * Usage:
 *   k6 run benchmarks/k6/stream_perf.js --env BASE_URL=http://localhost:8100/api/v1
 *   k6 run benchmarks/k6/stream_perf.js --env SCENARIO=load
 */

import http from "k6/http";
import { check, sleep } from "k6";
import { Counter, Trend, Rate } from "k6/metrics";

// --- Configuration ---
const BASE_URL = __ENV.BASE_URL || "http://localhost:8100/api/v1";
const CONSUMER_URL = __ENV.CONSUMER_URL || "http://localhost:9090";
const SCENARIO = __ENV.SCENARIO || __ENV.K6_SCENARIO || "smoke";

// --- Custom metrics ---
const eventDeliveryLatency = new Trend("event_delivery_latency_ms", true);
const eventsPublished = new Counter("events_published");
const eventsReceived = new Counter("events_received");
const eventsMissed = new Counter("events_missed");
const deliveryRate = new Rate("event_delivery_rate");

// --- Scenarios ---
const scenarios = {
  smoke: {
    executor: "shared-iterations",
    vus: 1,
    iterations: 10,
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
      { duration: "10s", target: 10 },
      { duration: "30s", target: 50 },
      { duration: "10s", target: 0 },
    ],
  },
};

export const options = {
  scenarios: {
    [SCENARIO]: scenarios[SCENARIO] || scenarios.smoke,
  },
  thresholds: {
    event_delivery_rate: [{ threshold: "rate>0.90", abortOnFail: false }],
    event_delivery_latency_ms: ["p(95)<2000"],
  },
};

// --- Helpers ---
const headers = { "Content-Type": "application/json" };

function createEntity(entity, payload) {
  const kebab = entity.replace(/_/g, "-");
  const url = `${BASE_URL}/${kebab}/`;
  const res = http.post(url, JSON.stringify(payload), { headers, timeout: "10s" });
  return res;
}

function pollConsumer(topic, expectedKey, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const res = http.get(`${CONSUMER_URL}/events?topic=${encodeURIComponent(topic)}&limit=100`, {
      timeout: "5s",
    });
    if (res.status === 200) {
      try {
        const events = JSON.parse(res.body);
        if (Array.isArray(events)) {
          const match = events.find((e) => e.key === expectedKey);
          if (match) {
            return match;
          }
        }
      } catch (_) {
        // parse error, retry
      }
    }
    sleep(0.1);
  }
  return null;
}

function getConsumerStats() {
  const res = http.get(`${CONSUMER_URL}/stats`, { timeout: "5s" });
  if (res.status === 200) {
    try {
      return JSON.parse(res.body);
    } catch (_) {
      return {};
    }
  }
  return {};
}

// --- Main test ---
export default function () {
  // Create a pet and verify the event was delivered
  const petPayload = {
    name: `stream-pet-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    status: "available",
  };

  const createStart = Date.now();
  const createRes = createEntity("pet", petPayload);

  const created = check(createRes, {
    "create returns 201": (r) => r.status === 201,
  });

  if (!created) {
    eventsMissed.add(1);
    deliveryRate.add(false);
    return;
  }

  eventsPublished.add(1);

  // Extract entity_id from response
  let entityId;
  try {
    const body = JSON.parse(createRes.body);
    const data = body.data || body;
    entityId = data.entity_id;
  } catch (_) {
    eventsMissed.add(1);
    deliveryRate.add(false);
    return;
  }

  // Poll consumer for the event
  // Topic format depends on the app:
  //   slip-stream: "slip-stream.pet.create"
  //   stellar-drive: "pet.created"
  // Try both patterns
  const topics = ["slip-stream.pet.create", "pet.created"];
  let delivered = null;

  for (const topic of topics) {
    delivered = pollConsumer(topic, entityId, 5000);
    if (delivered) break;
  }

  if (delivered) {
    eventsReceived.add(1);
    deliveryRate.add(true);

    // Calculate delivery latency
    const latency = delivered.received_at
      ? new Date(delivered.received_at).getTime() - createStart
      : Date.now() - createStart;
    eventDeliveryLatency.add(latency);
  } else {
    eventsMissed.add(1);
    deliveryRate.add(false);
  }
}

export function teardown() {
  // Log final consumer stats
  const stats = getConsumerStats();
  console.log(`Consumer stats: ${JSON.stringify(stats)}`);
}

export function handleSummary(data) {
  const summary = {
    scenario: SCENARIO,
    metrics: {
      events_published: data.metrics.events_published
        ? data.metrics.events_published.values.count
        : 0,
      events_received: data.metrics.events_received
        ? data.metrics.events_received.values.count
        : 0,
      events_missed: data.metrics.events_missed
        ? data.metrics.events_missed.values.count
        : 0,
      delivery_rate: data.metrics.event_delivery_rate
        ? data.metrics.event_delivery_rate.values.rate
        : 0,
      delivery_latency_p50: data.metrics.event_delivery_latency_ms
        ? data.metrics.event_delivery_latency_ms.values["p(50)"]
        : null,
      delivery_latency_p95: data.metrics.event_delivery_latency_ms
        ? data.metrics.event_delivery_latency_ms.values["p(95)"]
        : null,
    },
  };

  return {
    "benchmarks/results/stream-summary.json": JSON.stringify(summary, null, 2),
    stdout: textSummary(data, { indent: " ", enableColors: true }),
  };
}

function textSummary(data) {
  const lines = ["Stream Performance Summary", "=".repeat(40)];
  if (data.metrics.events_published) {
    lines.push(`Events published: ${data.metrics.events_published.values.count}`);
  }
  if (data.metrics.events_received) {
    lines.push(`Events received:  ${data.metrics.events_received.values.count}`);
  }
  if (data.metrics.event_delivery_rate) {
    lines.push(
      `Delivery rate:    ${(data.metrics.event_delivery_rate.values.rate * 100).toFixed(1)}%`
    );
  }
  if (data.metrics.event_delivery_latency_ms) {
    const m = data.metrics.event_delivery_latency_ms.values;
    lines.push(`Latency p50:      ${m["p(50)"]?.toFixed(1) || "N/A"} ms`);
    lines.push(`Latency p95:      ${m["p(95)"]?.toFixed(1) || "N/A"} ms`);
  }
  return lines.join("\n") + "\n";
}
