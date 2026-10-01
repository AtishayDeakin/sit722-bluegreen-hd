#!/usr/bin/env python3
"""
Blue/green deployment controller for the KoalaTech University application.

The pipeline (.github/workflows/ci-cd.yml) calls this script one step at a
time. Kubernetes itself is the single source of truth: the colour that the
production Service selects IS the live release, so no state file is needed.

    plan      work out which colour is live and which is idle
    deploy    deploy a release to the idle colour and point preview at it
    swap      switch production traffic to a colour (zero downtime)
    observe   watch Prometheus after the swap and decide healthy/unhealthy
    rollback  switch production back to the previous colour
    abort     clean up a release that never reached production
    finalize  promote the new release and retire the old colour
    status    print a human-readable view of both colours

Helpers used by the pipeline: preflight, registry-secret, admin-password.

Only the Python standard library is used, so it runs on the self-hosted
runner (macOS system Python 3.9+) without installing anything.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import string
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional


ROOT = Path(__file__).resolve().parent
COLOURS = ("blue", "green")
ANNOTATION = "bluegreen.koalatech.io/"

NAMESPACE = os.getenv("BG_NAMESPACE", "koalatech")
KUBE_CONTEXT = os.getenv("BG_KUBE_CONTEXT", "docker-desktop")
PROD_SERVICE = os.getenv("BG_PROD_SERVICE", "koalatech-prod")
PREVIEW_SERVICE = os.getenv("BG_PREVIEW_SERVICE", "koalatech-preview")
PROMETHEUS_NAMESPACE = os.getenv("BG_PROMETHEUS_NAMESPACE", "monitoring")
PROMETHEUS_SERVICE = os.getenv("BG_PROMETHEUS_SERVICE", "kps-prometheus:9090")
PULL_SECRET = "ghcr-pull"


class ControllerError(Exception):
    """A problem the pipeline should report clearly and stop on."""


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def other_colour(colour: str) -> str:
    if colour not in COLOURS:
        raise ControllerError(f"Unknown colour {colour!r}; expected blue or green.")
    return "green" if colour == "blue" else "blue"


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_services(path: Path = ROOT / "services.json") -> dict:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def log(message: str) -> None:
    print(message, flush=True)


def write_step_summary(markdown: str) -> None:
    """Append to the GitHub Actions job summary (no-op when run locally)."""
    summary_path = os.getenv("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as handle:
            handle.write(markdown.rstrip() + "\n\n")


def write_outputs(values: Dict[str, str]) -> None:
    """Expose values to later workflow steps (no-op when run locally)."""
    output_path = os.getenv("GITHUB_OUTPUT")
    if output_path:
        with open(output_path, "a", encoding="utf-8") as handle:
            for key, value in values.items():
                handle.write(f"{key}={value}\n")


# ---------------------------------------------------------------------------
# Kubernetes access
# ---------------------------------------------------------------------------

Runner = Callable[[List[str], Optional[str]], str]


def run_kubectl(args: List[str], stdin: Optional[str] = None) -> str:
    """Run kubectl against the pinned context, so we can never deploy to the
    wrong cluster by accident."""
    command = ["kubectl", "--context", KUBE_CONTEXT, *args]
    result = subprocess.run(
        command,
        input=stdin,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise ControllerError(
            f"kubectl {' '.join(args[:4])} failed:\n{result.stderr.strip()}"
        )
    return result.stdout


class Kube:
    def __init__(self, runner: Runner = run_kubectl, namespace: str = NAMESPACE):
        self.run = runner
        self.namespace = namespace

    def ns(self, *args: str) -> List[str]:
        return ["-n", self.namespace, *args]

    def get_json(self, *args: str) -> dict:
        return json.loads(self.run(self.ns("get", *args, "-o", "json"), None))

    def apply(self, manifest: str) -> str:
        return self.run(["apply", "-f", "-"], manifest)

    def service_selector_colour(self, service: str) -> Optional[str]:
        selector = self.get_json("service", service)["spec"].get("selector") or {}
        return selector.get("color")

    def service_annotations(self, service: str) -> Dict[str, str]:
        return self.get_json("service", service)["metadata"].get("annotations") or {}

    def point_service(self, service: str, colour: str) -> None:
        patch = {"spec": {"selector": {"app": "frontend", "color": colour}}}
        self.run(self.ns("patch", "service", service, "--type", "merge",
                         "-p", json.dumps(patch)), None)

    def annotate(self, service: str, values: Dict[str, str]) -> None:
        pairs = [f"{ANNOTATION}{key}={value}" for key, value in values.items()]
        self.run(self.ns("annotate", "service", service, "--overwrite", *pairs), None)

    def deployments(self, colour: str) -> List[dict]:
        data = self.get_json("deployments", "-l", f"color={colour}")
        return data.get("items", [])

    def colour_is_ready(self, colour: str, expected_count: int) -> bool:
        """True when every deployment of the colour exists, has at least one
        replica and all of its replicas are available."""
        items = self.deployments(colour)
        if len(items) < expected_count:
            return False
        for item in items:
            wanted = item["spec"].get("replicas", 0)
            available = item.get("status", {}).get("availableReplicas", 0) or 0
            if wanted < 1 or available < wanted:
                return False
        return True

    def scale_colour(self, colour: str, replicas: int) -> None:
        for item in self.deployments(colour):
            name = item["metadata"]["name"]
            self.run(self.ns("scale", "deployment", name,
                             f"--replicas={replicas}"), None)

    def pod_health(self, colour: str) -> Dict[str, int]:
        pods = self.get_json("pods", "-l", f"color={colour}").get("items", [])
        restarts = 0
        not_ready = 0
        for pod in pods:
            if pod.get("metadata", {}).get("deletionTimestamp"):
                continue
            statuses = pod.get("status", {}).get("containerStatuses", []) or []
            restarts += sum(s.get("restartCount", 0) for s in statuses)
            if not statuses or not all(s.get("ready") for s in statuses):
                not_ready += 1
        return {"pods": len(pods), "restarts": restarts, "not_ready": not_ready}

    def secret_value(self, name: str, key: str) -> str:
        data = self.get_json("secret", name)["data"]
        return base64.b64decode(data[key]).decode("utf-8")

    def prometheus_query(self, promql: str) -> Optional[float]:
        """Query Prometheus through the Kubernetes API server proxy. No port
        has to be exposed on the laptop and kubectl's credentials are reused."""
        path = (
            f"/api/v1/namespaces/{PROMETHEUS_NAMESPACE}/services/"
            f"{PROMETHEUS_SERVICE}/proxy/api/v1/query?"
            + urllib.parse.urlencode({"query": promql})
        )
        response = json.loads(self.run(["get", "--raw", path], None))
        if response.get("status") != "success":
            raise ControllerError(f"Prometheus query failed: {response}")
        result = response["data"]["result"]
        if not result:
            return None
        value = float(result[0]["value"][1])
        return None if value != value else value  # NaN -> no data


# ---------------------------------------------------------------------------
# Confirming what an endpoint really serves
#
# Changing a Service selector is not instant for clients: Kubernetes has to
# update the endpoints and Docker Desktop's localhost forwarding follows. During
# that gap new connections can still reach the OLD colour. So after every
# switch we ask the endpoint itself (/release) until it answers with the new
# colour several times in a row. Each probe opens a fresh connection.
# ---------------------------------------------------------------------------

def serving_colour(url: str, timeout: float = 3.0) -> Optional[str]:
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/release", timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8")).get("color")
    except Exception:
        return None


def wait_until_serving(url: str, colour: str, timeout: float = 120,
                       consecutive: int = 5, interval: float = 0.5) -> float:
    """Block until `url` serves `colour` `consecutive` times in a row.
    Returns the seconds it took; raises ControllerError on timeout."""
    started = time.monotonic()
    streak = 0
    while time.monotonic() - started < timeout:
        if serving_colour(url) == colour:
            streak += 1
            if streak >= consecutive:
                return time.monotonic() - started
        else:
            streak = 0
        time.sleep(interval)
    raise ControllerError(f"{url} was still not serving {colour} after {timeout:.0f}s.")


# ---------------------------------------------------------------------------
# Rendering the release manifests
# ---------------------------------------------------------------------------

def render_release(
    colour: str,
    version: str,
    registry: str,
    replicas: int,
    fault_rate: float,
    fault_delay: float,
    services: Optional[dict] = None,
    namespace: str = NAMESPACE,
) -> str:
    """Render every Deployment of one colour as a single multi-document YAML."""
    services = services or load_services()
    other_colour(colour)  # validates the colour
    if not version or not version.isalnum():
        raise ControllerError(f"Version must be a git SHA, got {version!r}.")

    common = {
        "COLOR": colour,
        "VERSION": version,
        "REGISTRY": registry.lower().rstrip("/"),
        "REPLICAS": str(replicas),
        "NAMESPACE": namespace,
        "FAULT_RATE": f"{fault_rate:g}",
        "FAULT_START_AFTER_SECONDS": f"{fault_delay:g}",
    }

    backend_template = string.Template((ROOT / "k8s/backend.yaml.tpl").read_text())
    frontend_template = string.Template((ROOT / "k8s/frontend.yaml.tpl").read_text())

    documents = []
    for name, spec in services["backends"].items():
        documents.append(backend_template.substitute(
            common, SERVICE=name, IMAGE=spec["image"], DATABASE=spec["database"]))
    documents.append(frontend_template.substitute(
        common, IMAGE=services["frontend"]["image"]))

    return "\n---\n".join(documents)


def deployment_names(colour: str, services: Optional[dict] = None) -> List[str]:
    services = services or load_services()
    return [f"{name}-{colour}" for name in services["backends"]] + [f"frontend-{colour}"]


# ---------------------------------------------------------------------------
# The health gate (pure logic, unit tested in deploy/tests)
# ---------------------------------------------------------------------------

@dataclass
class Thresholds:
    max_error_ratio: float = 0.05     # more than 5% of requests failing
    max_p95_seconds: float = 1.0      # 95th percentile latency
    min_rps: float = 0.5              # below this there is too little traffic to judge
    consecutive_failures: int = 2     # unhealthy samples in a row before rollback
    min_healthy_samples: int = 3      # healthy samples needed to promote


@dataclass
class Sample:
    elapsed: int
    rps: Optional[float]
    error_ratio: Optional[float]
    p95: Optional[float]
    restarts: int
    not_ready: int
    verdict: str = ""
    reasons: List[str] = field(default_factory=list)


def judge_sample(sample: Sample, limits: Thresholds) -> Sample:
    """Label one sample healthy, unhealthy or no-data."""
    reasons = []
    if sample.restarts > 0:
        reasons.append(f"{sample.restarts} container restart(s)")
    if sample.not_ready > 0:
        reasons.append(f"{sample.not_ready} pod(s) not ready")

    enough_traffic = sample.rps is not None and sample.rps >= limits.min_rps
    if enough_traffic:
        if sample.error_ratio is not None and sample.error_ratio > limits.max_error_ratio:
            reasons.append(
                f"error rate {sample.error_ratio:.1%} > {limits.max_error_ratio:.0%}")
        if sample.p95 is not None and sample.p95 > limits.max_p95_seconds:
            reasons.append(f"p95 latency {sample.p95:.2f}s > {limits.max_p95_seconds:.2f}s")

    if reasons:
        sample.verdict = "unhealthy"
    elif not enough_traffic:
        sample.verdict = "no-data"
        reasons.append("not enough traffic to judge")
    else:
        sample.verdict = "healthy"
    sample.reasons = reasons
    return sample


def gate_decision(samples: List[Sample], limits: Thresholds, finished: bool) -> str:
    """Return 'fail', 'pass' or 'continue' for the samples seen so far.

    Fails fast after N unhealthy samples in a row. When the window ends it
    only passes if enough healthy samples were seen; a release we could not
    verify is treated as a failure (fail-safe).
    """
    streak = 0
    for sample in samples:
        streak = streak + 1 if sample.verdict == "unhealthy" else 0
        if streak >= limits.consecutive_failures:
            return "fail"
    if not finished:
        return "continue"
    healthy = sum(1 for s in samples if s.verdict == "healthy")
    return "pass" if healthy >= limits.min_healthy_samples else "fail"


def promql_for(colour: str, namespace: str = NAMESPACE) -> Dict[str, str]:
    selector = f'namespace="{namespace}",color="{colour}"'
    return {
        "rps": f"sum(rate(http_requests_total{{{selector}}}[30s]))",
        "errors": f'sum(rate(http_requests_total{{{selector},status="5xx"}}[30s]))',
        "p95": (
            "histogram_quantile(0.95, sum by (le) "
            f"(rate(http_request_duration_seconds_bucket{{{selector}}}[30s])))"
        ),
    }


def format_number(value: Optional[float], pattern: str) -> str:
    return "-" if value is None else pattern.format(value)


def samples_table(samples: List[Sample]) -> str:
    rows = [
        "| t (s) | req/s | 5xx ratio | p95 (s) | restarts | not ready | verdict |",
        "|---:|---:|---:|---:|---:|---:|---|",
    ]
    for s in samples:
        rows.append(
            f"| {s.elapsed} | {format_number(s.rps, '{:.2f}')} "
            f"| {format_number(s.error_ratio, '{:.1%}')} "
            f"| {format_number(s.p95, '{:.3f}')} | {s.restarts} | {s.not_ready} "
            f"| {s.verdict}{': ' + '; '.join(s.reasons) if s.reasons else ''} |"
        )
    return "\n".join(rows)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_preflight(kube: Kube, _args) -> int:
    log(f"Kubernetes context : {KUBE_CONTEXT}")
    kube.run(["version", "-o", "json"], None)
    kube.run(["get", "namespace", kube.namespace], None)
    for service in (PROD_SERVICE, PREVIEW_SERVICE):
        kube.get_json("service", service)
    kube.prometheus_query("vector(1)")
    log("Preflight OK: cluster reachable, platform provisioned, Prometheus answering.")
    return 0


def cmd_registry_secret(kube: Kube, args) -> int:
    """Create/refresh the image pull secret from environment variables, so the
    token never appears on a command line or in the logs."""
    username = os.environ["REGISTRY_USERNAME"]
    password = os.environ["REGISTRY_PASSWORD"]
    auth = base64.b64encode(f"{username}:{password}".encode()).decode()
    config = {"auths": {args.server: {"username": username, "password": password, "auth": auth}}}
    manifest = {
        "apiVersion": "v1",
        "kind": "Secret",
        "type": "kubernetes.io/dockerconfigjson",
        "metadata": {"name": PULL_SECRET, "namespace": kube.namespace},
        "data": {".dockerconfigjson": base64.b64encode(json.dumps(config).encode()).decode()},
    }
    kube.apply(json.dumps(manifest))
    log(f"Image pull secret '{PULL_SECRET}' is up to date for {args.server}.")
    return 0


def cmd_admin_password(kube: Kube, _args) -> int:
    print(kube.secret_value("koalatech-secrets", "DEFAULT_ADMIN_PASSWORD"))
    return 0


def cmd_plan(kube: Kube, _args) -> int:
    services = load_services()
    expected = len(deployment_names("blue", services))
    live = kube.service_selector_colour(PROD_SERVICE) or "blue"
    target = other_colour(live)
    live_ready = kube.colour_is_ready(live, expected)
    annotations = kube.service_annotations(PROD_SERVICE)
    live_version = annotations.get(ANNOTATION + "live-version", "none")

    if not live_ready:
        log(f"No healthy release is serving production ({live} is empty). "
            f"This is a first deployment, so there is nothing to roll back to.")

    log(f"Live colour   : {live} (version {live_version}, ready={live_ready})")
    log(f"Target colour : {target}")
    write_outputs({
        "live_color": live,
        "target_color": target,
        "live_version": live_version,
        "has_previous": "true" if live_ready else "false",
    })
    write_step_summary(
        "### Release plan\n"
        f"| | colour | version |\n|---|---|---|\n"
        f"| currently live | **{live}** | `{live_version}` |\n"
        f"| deploying to | **{target}** | `{os.getenv('GITHUB_SHA', 'local')}` |\n"
    )
    return 0


def cmd_deploy(kube: Kube, args) -> int:
    services = load_services()
    manifest = render_release(
        colour=args.color,
        version=args.version,
        registry=args.registry,
        replicas=args.replicas,
        fault_rate=args.fault_rate,
        fault_delay=args.fault_delay,
        services=services,
    )
    log(f"Applying {args.color} release {args.version[:7]} "
        f"({len(deployment_names(args.color, services))} deployments)...")
    log(kube.apply(manifest).strip())

    for name in deployment_names(args.color, services):
        log(f"Waiting for {name} to become ready...")
        try:
            kube.run(kube.ns("rollout", "status", f"deployment/{name}",
                             f"--timeout={args.timeout}s"), None)
        except ControllerError:
            log(kube.run(kube.ns("get", "pods", "-l", f"color={args.color}", "-o", "wide"), None))
            raise ControllerError(f"{name} did not become ready within {args.timeout}s.")

    kube.point_service(PREVIEW_SERVICE, args.color)
    if getattr(args, "preview_url", None):
        took = wait_until_serving(args.preview_url, args.color, timeout=args.wait_timeout)
        log(f"Preview endpoint confirmed serving {args.color} after {took:.1f}s.")
    fault_note = (
        f" Fault injection ON: {args.fault_rate:.0%} of requests, after {args.fault_delay:g}s."
        if args.fault_rate > 0 else ""
    )
    log(f"{args.color} is ready and the preview endpoint now points at it.{fault_note}")
    write_step_summary(
        f"### Staged on {args.color}\nAll deployments ready; preview endpoint switched to "
        f"**{args.color}** for smoke testing.{fault_note}"
    )
    return 0


def cmd_swap(kube: Kube, args) -> int:
    services = load_services()
    expected = len(deployment_names(args.to, services))
    if not kube.colour_is_ready(args.to, expected):
        raise ControllerError(f"Refusing to swap: {args.to} is not fully ready.")

    previous = kube.service_selector_colour(PROD_SERVICE)
    annotations = kube.service_annotations(PROD_SERVICE)
    started = time.monotonic()
    kube.point_service(PROD_SERVICE, args.to)
    elapsed_ms = (time.monotonic() - started) * 1000

    effective = ""
    if getattr(args, "prod_url", None):
        try:
            took = wait_until_serving(args.prod_url, args.to, timeout=args.wait_timeout)
        except ControllerError:
            if previous:
                kube.point_service(PROD_SERVICE, previous)
            raise ControllerError(
                f"Production never started serving {args.to}; selector put back to {previous}.")
        effective = f" Users confirmed on {args.to} after {took:.1f}s."

    kube.annotate(PROD_SERVICE, {
        "live-color": args.to,
        "live-version": args.version,
        "previous-color": previous or "none",
        "previous-version": annotations.get(ANNOTATION + "live-version", "none"),
        "last-transition": now_iso(),
        "last-result": "observing",
    })
    log(f"Production traffic switched {previous} -> {args.to} "
        f"(selector update took {elapsed_ms:.0f} ms).{effective}")
    write_step_summary(
        f"### Traffic switched\nProduction now serves **{args.to}** "
        f"(was {previous}).{effective} Old colour kept running for instant rollback."
    )
    return 0


def cmd_observe(kube: Kube, args) -> int:
    limits = Thresholds(
        max_error_ratio=args.max_error_ratio,
        max_p95_seconds=args.max_p95,
        min_rps=args.min_rps,
        consecutive_failures=args.consecutive,
        min_healthy_samples=args.min_healthy,
    )
    queries = promql_for(args.color)
    baseline_restarts = kube.pod_health(args.color)["restarts"]
    samples: List[Sample] = []
    started = time.monotonic()

    log(f"Observing {args.color} for {args.duration}s (sample every {args.interval}s). "
        f"Rollback if error rate > {limits.max_error_ratio:.0%} or p95 > "
        f"{limits.max_p95_seconds}s for {limits.consecutive_failures} samples in a row.")

    decision = "continue"
    while decision == "continue":
        time.sleep(args.interval)
        elapsed = int(time.monotonic() - started)

        rps = kube.prometheus_query(queries["rps"])
        errors = kube.prometheus_query(queries["errors"]) or 0.0
        p95 = kube.prometheus_query(queries["p95"])
        health = kube.pod_health(args.color)

        sample = judge_sample(Sample(
            elapsed=elapsed,
            rps=rps,
            error_ratio=(errors / rps) if rps else None,
            p95=p95,
            restarts=max(health["restarts"] - baseline_restarts, 0),
            not_ready=health["not_ready"],
        ), limits)
        samples.append(sample)
        log(f"[{elapsed:>4}s] rps={format_number(sample.rps, '{:.2f}')} "
            f"5xx={format_number(sample.error_ratio, '{:.1%}')} "
            f"p95={format_number(sample.p95, '{:.3f}')}s "
            f"restarts={sample.restarts} not_ready={sample.not_ready} -> {sample.verdict}"
            f"{' (' + '; '.join(sample.reasons) + ')' if sample.reasons else ''}")

        decision = gate_decision(samples, limits, finished=elapsed >= args.duration)

    reason = ""
    if decision == "fail":
        bad = [s for s in samples if s.verdict == "unhealthy"]
        reason = bad[-1].reasons[0] if bad else "not enough healthy traffic to verify the release"

    write_outputs({"verdict": decision, "reason": reason.replace("\n", " ")})
    headline = ("PASSED: release is healthy" if decision == "pass"
                else f"FAILED: {reason}")
    write_step_summary(f"### Post-swap health gate: {headline}\n{samples_table(samples)}")
    log(f"Health gate {headline}")
    return 0 if decision == "pass" else 1


def cmd_rollback(kube: Kube, args) -> int:
    services = load_services()
    expected = len(deployment_names(args.to, services))
    reason = args.reason or "post-deployment health gate failed"

    if not kube.colour_is_ready(args.to, expected):
        kube.annotate(PROD_SERVICE, {"last-result": "failed-no-rollback-target",
                                     "last-transition": now_iso()})
        raise ControllerError(
            f"Cannot roll back: {args.to} has no healthy release (first deployment).")

    annotations = kube.service_annotations(PROD_SERVICE)
    kube.point_service(PROD_SERVICE, args.to)
    kube.annotate(PROD_SERVICE, {
        "live-color": args.to,
        "live-version": annotations.get(ANNOTATION + "previous-version", "unknown"),
        "previous-color": args.from_colour,
        "previous-version": annotations.get(ANNOTATION + "live-version", "unknown"),
        "last-transition": now_iso(),
        "last-result": "rolled-back",
    })
    log(f"ROLLED BACK: production traffic returned to {args.to}. Reason: {reason}")
    restored = ""
    if getattr(args, "prod_url", None):
        try:
            took = wait_until_serving(args.prod_url, args.to, timeout=args.wait_timeout)
            restored = f" Users were back on {args.to} {took:.1f}s after the rollback started."
            log(restored.strip())
        except ControllerError as error:
            log(f"WARNING: {error}")

    diagnostics = kube.run(kube.ns("get", "pods", "-l", f"color={args.from_colour}"), None)
    kube.scale_colour(args.from_colour, 0)
    log(f"Faulty {args.from_colour} release scaled down to 0 replicas.")
    write_step_summary(
        f"## Automatic rollback\nProduction returned to **{args.to}** "
        f"with no manual action.{restored}\n\n**Reason:** {reason}\n\n"
        f"Faulty **{args.from_colour}** pods at the time of rollback:\n"
        f"```\n{diagnostics.strip()}\n```"
    )
    return 0


def cmd_abort(kube: Kube, args) -> int:
    kube.scale_colour(args.color, 0)
    log(f"Release on {args.color} abandoned before reaching production; scaled to 0. "
        f"Production was never changed.")
    write_step_summary(
        f"## Release blocked before production\nThe {args.color} release failed its "
        f"pre-production checks, so traffic was **never** switched. Production "
        f"kept serving the existing release."
    )
    return 0


def cmd_finalize(kube: Kube, args) -> int:
    kube.annotate(PROD_SERVICE, {"last-result": "promoted", "last-transition": now_iso()})
    if args.retire and args.retire != args.live:
        kube.scale_colour(args.retire, 0)
        log(f"Retired the previous release on {args.retire} (scaled to 0).")
    log(f"Release on {args.live} promoted.")
    write_step_summary(f"## Release promoted\n**{args.live}** is now the stable production release.")
    return 0


def cmd_status(kube: Kube, _args) -> int:
    services = load_services()
    live = kube.service_selector_colour(PROD_SERVICE)
    preview = kube.service_selector_colour(PREVIEW_SERVICE)
    annotations = kube.service_annotations(PROD_SERVICE)
    log(f"production -> {live}    preview -> {preview}")
    for key in ("live-version", "previous-color", "previous-version",
                "last-result", "last-transition"):
        log(f"  {key:<17} {annotations.get(ANNOTATION + key, '-')}")
    for colour in COLOURS:
        items = kube.deployments(colour)
        ready = kube.colour_is_ready(colour, len(deployment_names(colour, services)))
        versions = sorted({i["metadata"]["labels"].get("version", "?")[:7] for i in items})
        log(f"  {colour:<6} deployments={len(items)} ready={ready} versions={versions or '-'}")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("preflight")
    sub.add_parser("plan")
    sub.add_parser("status")
    sub.add_parser("admin-password")

    p = sub.add_parser("registry-secret")
    p.add_argument("--server", default="ghcr.io")

    p = sub.add_parser("deploy")
    p.add_argument("--color", required=True, choices=COLOURS)
    p.add_argument("--version", required=True)
    p.add_argument("--registry", required=True)
    p.add_argument("--replicas", type=int, default=2)
    p.add_argument("--fault-rate", type=float, default=0.0)
    p.add_argument("--fault-delay", type=float, default=0.0)
    p.add_argument("--timeout", type=int, default=300)
    p.add_argument("--preview-url", default=os.getenv("BG_PREVIEW_URL", "http://localhost:8081"))
    p.add_argument("--wait-timeout", type=float, default=120)

    p = sub.add_parser("swap")
    p.add_argument("--to", required=True, choices=COLOURS)
    p.add_argument("--version", required=True)
    p.add_argument("--prod-url", default=os.getenv("BG_PROD_URL", "http://localhost:8080"))
    p.add_argument("--wait-timeout", type=float, default=120)

    p = sub.add_parser("observe")
    p.add_argument("--color", required=True, choices=COLOURS)
    p.add_argument("--duration", type=int, default=150)
    p.add_argument("--interval", type=int, default=10)
    p.add_argument("--max-error-ratio", type=float, default=0.05)
    p.add_argument("--max-p95", type=float, default=1.0)
    p.add_argument("--min-rps", type=float, default=0.5)
    p.add_argument("--consecutive", type=int, default=2)
    p.add_argument("--min-healthy", type=int, default=3)

    p = sub.add_parser("rollback")
    p.add_argument("--from", dest="from_colour", required=True, choices=COLOURS)
    p.add_argument("--to", required=True, choices=COLOURS)
    p.add_argument("--reason", default="")
    p.add_argument("--prod-url", default=os.getenv("BG_PROD_URL", "http://localhost:8080"))
    p.add_argument("--wait-timeout", type=float, default=60)

    p = sub.add_parser("abort")
    p.add_argument("--color", required=True, choices=COLOURS)

    p = sub.add_parser("finalize")
    p.add_argument("--live", required=True, choices=COLOURS)
    p.add_argument("--retire", choices=COLOURS)

    return parser


COMMANDS = {
    "preflight": cmd_preflight,
    "registry-secret": cmd_registry_secret,
    "admin-password": cmd_admin_password,
    "plan": cmd_plan,
    "deploy": cmd_deploy,
    "swap": cmd_swap,
    "observe": cmd_observe,
    "rollback": cmd_rollback,
    "abort": cmd_abort,
    "finalize": cmd_finalize,
    "status": cmd_status,
}


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return COMMANDS[args.command](Kube(), args)
    except ControllerError as error:
        log(f"ERROR: {error}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
