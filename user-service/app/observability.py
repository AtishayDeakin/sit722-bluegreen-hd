"""
Observability hooks used by the blue/green deployment pipeline.

Every backend service calls setup_observability() once at start-up. It adds:

  /metrics  Prometheus metrics (request count by status class, latency).
            The deployment controller reads these to decide whether a new
            release is healthy after traffic has been switched to it.
  /ready    Readiness probe. Unlike /health it also checks the database, so
            Kubernetes only sends traffic to a pod that can really serve it.
  /version  Which release (git SHA) and which colour (blue/green) this pod is.

It also adds an optional fault-injection middleware. It is switched off by
default and is only turned on for a release through the FAULT_RATE and
FAULT_START_AFTER_SECONDS environment variables. This lets the pipeline's
safety gates (smoke tests and the post-swap error-rate check) be demonstrated
with a controlled, repeatable failure instead of breaking real code.
"""

import logging
import os
import random
import time

from fastapi import FastAPI, Request, Response, status
from fastapi.responses import JSONResponse
from prometheus_fastapi_instrumentator import Instrumentator
from sqlalchemy import text
from sqlalchemy.engine import Engine
from starlette.middleware.base import BaseHTTPMiddleware


logger = logging.getLogger(__name__)

PROCESS_STARTED_AT = time.monotonic()

# Probe and telemetry endpoints are never failed on purpose and are not
# counted in the request metrics, so they cannot hide or fake an error rate.
UNINSTRUMENTED_PATHS = ["/health", "/ready", "/metrics", "/version"]


def _float_from_env(name: str, default: float) -> float:
    raw_value = os.getenv(name, "")

    if raw_value.strip() == "":
        return default

    try:
        return float(raw_value)
    except ValueError:
        logger.warning(
            "Ignoring invalid value %r for %s.",
            raw_value,
            name,
        )
        return default


class FaultInjectionMiddleware(BaseHTTPMiddleware):
    """Returns HTTP 500 for a share of real API requests.

    fault_rate          share of requests to fail, from 0.0 to 1.0
    start_after_seconds how long after process start the faults begin.
                        A delay simulates a defect that only shows up under
                        real traffic (it passes smoke tests, then degrades).
    """

    def __init__(
        self,
        app,
        fault_rate: float,
        start_after_seconds: float,
    ) -> None:
        super().__init__(app)
        self.fault_rate = fault_rate
        self.start_after_seconds = start_after_seconds

    def faults_active(self) -> bool:
        elapsed = time.monotonic() - PROCESS_STARTED_AT
        return elapsed >= self.start_after_seconds

    async def dispatch(self, request: Request, call_next):
        if (
            request.url.path not in UNINSTRUMENTED_PATHS
            and self.faults_active()
            and random.random() < self.fault_rate
        ):
            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={
                    "detail": "Injected fault (fault injection is enabled for this release)."
                },
            )

        return await call_next(request)


def setup_observability(
    app: FastAPI,
    engine: Engine,
    service_name: str,
) -> None:
    release_version = os.getenv("APP_VERSION", "dev")
    release_colour = os.getenv("APP_COLOR", "local")

    fault_rate = min(max(_float_from_env("FAULT_RATE", 0.0), 0.0), 1.0)
    fault_delay = max(_float_from_env("FAULT_START_AFTER_SECONDS", 0.0), 0.0)

    # Middleware order matters: Starlette runs the last added middleware
    # first. The fault middleware is added before the instrumentator so the
    # instrumentator wraps it and counts injected 500s like real errors.
    if fault_rate > 0:
        logger.warning(
            "FAULT INJECTION ENABLED for %s: %.0f%% of API requests will "
            "fail, starting %.0f seconds after start-up.",
            service_name,
            fault_rate * 100,
            fault_delay,
        )

        app.add_middleware(
            FaultInjectionMiddleware,
            fault_rate=fault_rate,
            start_after_seconds=fault_delay,
        )

    Instrumentator(
        should_group_status_codes=True,
        should_ignore_untemplated=True,
        excluded_handlers=UNINSTRUMENTED_PATHS,
    ).instrument(app).expose(
        app,
        endpoint="/metrics",
        include_in_schema=False,
    )

    @app.get("/ready", tags=["Health"])
    def readiness_check(response: Response) -> dict[str, str]:
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
        except Exception:
            logger.exception("Readiness check failed: database unavailable.")
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
            return {
                "status": "not-ready",
                "service": service_name,
            }

        return {
            "status": "ready",
            "service": service_name,
        }

    @app.get("/version", tags=["Health"])
    def version_info() -> dict[str, str]:
        return {
            "service": service_name,
            "version": release_version,
            "color": release_colour,
        }
