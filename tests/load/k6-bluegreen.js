// k6 load test that runs against PRODUCTION while the pipeline switches
// traffic from one colour to the other.
//
// It answers two questions with evidence instead of claims:
//   1. Zero downtime: did any request fail to connect during the switch?
//      (transport_errors must stay at 0)
//   2. Did traffic really move? served_by{color:...} counts which colour
//      answered each request, so the summary shows blue -> green.
//
// The pipeline runs it with Docker (no install needed):
//   docker run --rm -v "$PWD/tests/load:/scripts" -v "$OUT:/out" \
//     -e BASE_URL=http://host.docker.internal:8080 -e ADMIN_PASSWORD=... \
//     grafana/k6:1.8.1 run /scripts/k6-bluegreen.js

import http from "k6/http";
import { sleep } from "k6";
import { Counter } from "k6/metrics";

const BASE_URL = __ENV.BASE_URL || "http://host.docker.internal:8080";
const DURATION = __ENV.DURATION || "3m";
const RATE = parseInt(__ENV.RATE || "15", 10);
const OUT_DIR = __ENV.OUT_DIR || "/out";

export const options = {
  scenarios: {
    steady_users: {
      executor: "constant-arrival-rate",
      rate: RATE,
      timeUnit: "1s",
      duration: DURATION,
      preAllocatedVUs: 20,
      maxVUs: 60,
    },
  },
  thresholds: {
    // The zero-downtime assertion. Status 0 means the connection was refused,
    // reset or timed out, which is what users see as downtime.
    transport_errors: ["count==0"],
    // Declared so the per-colour counts appear in the summary.
    "served_by{color:blue}": ["count>=0"],
    "served_by{color:green}": ["count>=0"],
  },
  summaryTrendStats: ["avg", "p(95)", "max"],
};

const transportErrors = new Counter("transport_errors");
const serverErrors = new Counter("http_5xx");
const servedBy = new Counter("served_by");

function record(response) {
  if (response.status === 0) {
    transportErrors.add(1);
  } else if (response.status >= 500) {
    serverErrors.add(1);
  }
  return response;
}

export function setup() {
  // Retry for up to 2 minutes: on the very first release production has no
  // pods until the pipeline switches traffic, so login only works after that.
  for (let attempt = 1; attempt <= 60; attempt++) {
    const response = http.post(`${BASE_URL}/api/users/auth/login`, {
      username: __ENV.ADMIN_USERNAME || "admin",
      password: __ENV.ADMIN_PASSWORD,
    });
    if (response.status === 200) {
      return { token: response.json("access_token") };
    }
    sleep(2);
  }
  throw new Error("Could not log in to production to start the load test.");
}

export default function (data) {
  const release = record(http.get(`${BASE_URL}/release`, { tags: { name: "release" } }));
  if (release.status === 200) {
    servedBy.add(1, { color: release.json("color") });
  }

  const auth = { headers: { Authorization: `Bearer ${data.token}` } };
  record(http.get(`${BASE_URL}/api/courses/courses`, { ...auth, tags: { name: "courses" } }));
  record(http.get(`${BASE_URL}/api/students/students`, { ...auth, tags: { name: "students" } }));
}

function value(metrics, name, field = "count") {
  return metrics[name] ? metrics[name].values[field] || 0 : 0;
}

export function handleSummary(data) {
  const m = data.metrics;
  const report = {
    requests: value(m, "http_reqs"),
    transport_errors: value(m, "transport_errors"),
    http_5xx: value(m, "http_5xx"),
    served_by_blue: value(m, "served_by{color:blue}"),
    served_by_green: value(m, "served_by{color:green}"),
    p95_ms: Math.round(value(m, "http_req_duration", "p(95)")),
  };

  const text = [
    "",
    "=== Blue/green load test ===",
    `requests sent        : ${report.requests}`,
    `served by blue       : ${report.served_by_blue}`,
    `served by green      : ${report.served_by_green}`,
    `connection failures  : ${report.transport_errors}   <- must be 0 for zero downtime`,
    `HTTP 5xx responses   : ${report.http_5xx}`,
    `p95 latency          : ${report.p95_ms} ms`,
    "",
  ].join("\n");

  return {
    [`${OUT_DIR}/k6-summary.json`]: JSON.stringify(report, null, 2),
    stdout: text,
  };
}
