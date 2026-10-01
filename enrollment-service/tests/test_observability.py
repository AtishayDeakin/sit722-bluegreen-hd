"""Tests for the hooks the blue/green pipeline depends on."""

from fastapi import FastAPI
from fastapi.testclient import TestClient
from prometheus_client import CollectorRegistry
from prometheus_fastapi_instrumentator import Instrumentator

from app.observability import FaultInjectionMiddleware


def test_metrics_endpoint_exposes_request_counter(client):
    client.get("/")

    response = client.get("/metrics")

    assert response.status_code == 200
    assert "http_requests_total" in response.text


def test_probe_endpoints_are_not_counted_in_metrics(client):
    client.get("/health")
    client.get("/ready")

    metrics = client.get("/metrics").text

    assert 'handler="/health"' not in metrics
    assert 'handler="/ready"' not in metrics


def test_readiness_checks_the_database(client):
    response = client.get("/ready")

    assert response.status_code == 200
    assert response.json()["status"] == "ready"


def test_version_reports_release_and_colour(client):
    response = client.get("/version")

    assert response.status_code == 200
    assert set(response.json()) == {"service", "version", "color"}


def _app_with_faults(fault_rate: float, start_after_seconds: float) -> FastAPI:
    app = FastAPI()

    @app.get("/work")
    def work() -> dict[str, str]:
        return {"result": "ok"}

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "healthy"}

    app.add_middleware(
        FaultInjectionMiddleware,
        fault_rate=fault_rate,
        start_after_seconds=start_after_seconds,
    )
    # Same order as setup_observability(): faults first, then the
    # instrumentator, so injected 500s are counted as real errors.
    # A private registry keeps this test app separate from the service.
    Instrumentator(
        should_group_status_codes=True,
        registry=CollectorRegistry(),
    ).instrument(app).expose(app)

    return app


def test_fault_injection_fails_api_requests_and_is_counted():
    client = TestClient(_app_with_faults(1.0, 0))

    assert client.get("/work").status_code == 500
    assert 'status="5xx"' in client.get("/metrics").text


def test_fault_injection_never_fails_health_probes():
    client = TestClient(_app_with_faults(1.0, 0))

    assert client.get("/health").status_code == 200


def test_fault_injection_waits_for_its_start_delay():
    client = TestClient(_app_with_faults(1.0, 3600))

    assert client.get("/work").status_code == 200
