#!/usr/bin/env bash
# Runs the k6 zero-downtime load test in Docker on the self-hosted runner.
#
#   loadtest.sh start <seconds>   start k6 in the background against production
#   loadtest.sh finish            wait for k6 to end normally, then report
#   loadtest.sh stop              stop k6 early (after a rollback), then report
#   loadtest.sh cleanup           remove the container
#
# Reports go to the job summary and to $RUNNER_TEMP/k6/k6-summary.json.
set -euo pipefail

ACTION="${1:?start|finish|stop|cleanup}"
NAME="k6-${GITHUB_RUN_ID:-local}-${GITHUB_RUN_ATTEMPT:-1}"
OUT="${RUNNER_TEMP:-/tmp}/k6"
K6_IMAGE="grafana/k6:1.8.1"
PROD_URL="${PROD_URL:-http://host.docker.internal:8080}"

report() {
  docker logs "$NAME" 2>/dev/null | sed -n '/=== Blue\/green load test ===/,$p' || true
  if [[ -f "$OUT/k6-summary.json" ]]; then
    python3 - "$OUT/k6-summary.json" <<'PY'
import json, os, sys
r = json.load(open(sys.argv[1]))
summary = os.getenv("GITHUB_STEP_SUMMARY")
lines = [
    "### Load test during the release (k6)",
    "| metric | value |", "|---|---:|",
    f"| requests sent | {r['requests']} |",
    f"| answered by **blue** | {r['served_by_blue']} |",
    f"| answered by **green** | {r['served_by_green']} |",
    f"| connection failures (downtime) | **{r['transport_errors']}** |",
    f"| HTTP 5xx | {r['http_5xx']} |",
    f"| p95 latency | {r['p95_ms']} ms |",
]
if summary:
    open(summary, "a").write("\n".join(lines) + "\n\n")
gh_out = os.getenv("GITHUB_OUTPUT")
if gh_out:
    open(gh_out, "a").write(f"transport_errors={r['transport_errors']}\n")
PY
  else
    echo "No k6 summary was produced."
  fi
}

case "$ACTION" in
  start)
    SECONDS_TOTAL="${2:?duration in seconds}"
    mkdir -p "$OUT" && rm -f "$OUT/k6-summary.json"
    docker rm -f "$NAME" >/dev/null 2>&1 || true
    # The admin password comes from the environment, never from the command line.
    docker run -d --name "$NAME" \
      --add-host=host.docker.internal:host-gateway \
      -v "$PWD/tests/load:/scripts:ro" \
      -v "$OUT:/out" \
      -e BASE_URL="$PROD_URL" \
      -e ADMIN_PASSWORD \
      -e DURATION="${SECONDS_TOTAL}s" \
      -e RATE="${K6_RATE:-15}" \
      "$K6_IMAGE" run --quiet /scripts/k6-bluegreen.js >/dev/null
    echo "k6 is sending ${K6_RATE:-15} iterations/s to production for ${SECONDS_TOTAL}s."
    ;;
  finish)
    echo "Waiting for the load test to finish..."
    docker wait "$NAME" >/dev/null || true
    report
    ;;
  stop)
    # Keep measuring for a few seconds after the rollback, then stop k6.
    # SIGINT makes k6 end gracefully and still write its summary.
    sleep "${STOP_AFTER:-15}"
    docker kill --signal=SIGINT "$NAME" >/dev/null 2>&1 || true
    docker wait "$NAME" >/dev/null 2>&1 || true
    report
    ;;
  cleanup)
    docker rm -f "$NAME" >/dev/null 2>&1 || true
    ;;
  *)
    echo "Unknown action $ACTION" >&2; exit 2 ;;
esac
