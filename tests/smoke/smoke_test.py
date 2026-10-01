#!/usr/bin/env python3
"""
Smoke tests for a staged blue/green release.

The pipeline runs these against the PREVIEW endpoint, which points at the
idle colour, before any production traffic is switched. If one check fails,
the swap never happens and production is untouched.

The checks go through the frontend's nginx exactly like a real user would,
so they prove the whole colour works together: routing, every backend,
the shared databases and authentication.

Usage:
    SMOKE_ADMIN_PASSWORD=... python3 tests/smoke/smoke_test.py \
        --base-url http://localhost:8081 --expect-color green --expect-version <sha>

Standard library only, so it runs on the self-hosted runner without setup.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable, List, Optional, Tuple

BACKENDS = {
    "users": "user-service",
    "students": "student-service",
    "lecturers": "lecturer-service",
    "courses": "course-service",
    "enrollments": "enrollment-service",
}


class CheckFailed(Exception):
    pass


class Client:
    def __init__(self, base_url: str, timeout: float = 10.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.token: Optional[str] = None

    def request(self, method: str, path: str, body=None, form=None,
                retries: int = 3) -> Tuple[int, bytes]:
        headers = {"Accept": "application/json"}
        data = None
        if form is not None:
            data = urllib.parse.urlencode(form).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        elif body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        last_error = None
        for attempt in range(retries):
            req = urllib.request.Request(self.base_url + path, data=data,
                                         headers=headers, method=method)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as response:
                    return response.status, response.read()
            except urllib.error.HTTPError as error:
                return error.code, error.read()
            except (urllib.error.URLError, ConnectionError, TimeoutError) as error:
                # Only network-level errors are retried, never HTTP errors.
                last_error = error
                time.sleep(2 * (attempt + 1))
        raise CheckFailed(f"{method} {path}: no response ({last_error})")

    def json(self, method: str, path: str, expected: int = 200, **kwargs):
        status, raw = self.request(method, path, **kwargs)
        if status != expected:
            raise CheckFailed(f"{method} {path} returned {status}, expected {expected}: "
                              f"{raw[:200].decode(errors='replace')}")
        return json.loads(raw) if raw else None


def build_checks(client: Client, colour: str, version: str,
                 username: str, password: str) -> List[Tuple[str, Callable[[], str]]]:
    checks: List[Tuple[str, Callable[[], str]]] = []

    def frontend_page() -> str:
        status, raw = client.request("GET", "/")
        if status != 200 or b'id="root"' not in raw:
            raise CheckFailed(f"frontend returned {status} without the React root element")
        return "React app served by nginx"

    def release_identity() -> str:
        release = client.json("GET", "/release")
        if colour and release.get("color") != colour:
            raise CheckFailed(f"preview is serving {release.get('color')}, expected {colour}")
        if version and release.get("version") != version:
            raise CheckFailed(f"preview is serving {release.get('version')}, expected {version}")
        return f"{release['color']} @ {release['version'][:7]}"

    checks.append(("Frontend page loads", frontend_page))
    checks.append(("Preview serves the expected release", release_identity))

    for prefix, service in BACKENDS.items():
        def backend_identity(prefix=prefix, service=service) -> str:
            info = client.json("GET", f"/api/{prefix}/version")
            if info.get("service") != service:
                raise CheckFailed(f"/api/{prefix} reached {info.get('service')}, not {service}")
            if colour and info.get("color") != colour:
                raise CheckFailed(f"{service} answered from {info.get('color')} "
                                  f"(colours are mixed)")
            if version and info.get("version") != version:
                raise CheckFailed(f"{service} is running {info.get('version')}")
            ready = client.json("GET", f"/api/{prefix}/ready")
            return f"{info['color']} @ {info['version'][:7]}, database {ready['status']}"

        checks.append((f"{service} reachable, same colour, DB ready", backend_identity))

    def login() -> str:
        result = client.json("POST", "/api/users/auth/login",
                             form={"username": username, "password": password})
        client.token = result["access_token"]
        me = client.json("GET", "/api/users/auth/me")
        if me.get("role") != "admin":
            raise CheckFailed(f"logged in as role {me.get('role')}, expected admin")
        return f"JWT issued for {me['username']}"

    checks.append(("Admin can log in (user-service issues JWT)", login))

    for prefix in ("students", "lecturers", "courses", "enrollments"):
        def list_records(prefix=prefix) -> str:
            if not client.token:
                raise CheckFailed("skipped: no token from the login check")
            records = client.json("GET", f"/api/{prefix}/{prefix}")
            if not isinstance(records, list):
                raise CheckFailed("did not return a list")
            return f"{len(records)} record(s); JWT accepted across services"

        checks.append((f"List {prefix} with the token", list_records))

    def write_round_trip() -> str:
        if not client.token:
            raise CheckFailed("skipped: no token from the login check")
        course_id = f"SMK{(version or 'local')[:7]}".upper()
        course = {
            "course_id": course_id,
            "course_code": course_id,
            "course_name": "Smoke test course (deleted automatically)",
            "semester": "Smoke",
            "academic_year": 2026,
        }
        client.request("DELETE", f"/api/courses/courses/{course_id}")  # leftovers from a rerun
        try:
            client.json("POST", "/api/courses/courses", expected=201, body=course)
            stored = client.json("GET", f"/api/courses/courses/{course_id}")
            if stored["course_name"] != course["course_name"]:
                raise CheckFailed("stored course does not match what was written")
        finally:
            client.request("DELETE", f"/api/courses/courses/{course_id}")
        status, _ = client.request("GET", f"/api/courses/courses/{course_id}")
        if status != 404:
            raise CheckFailed("temporary course was not cleaned up")
        return "create, read and delete a course"

    checks.append(("Database write round trip", write_round_trip))
    return checks


def write_summary(results: List[Tuple[str, bool, str]], base_url: str) -> None:
    path = os.getenv("GITHUB_STEP_SUMMARY")
    if not path:
        return
    lines = [f"### Smoke tests against preview ({base_url})",
             "| check | result | detail |", "|---|---|---|"]
    for name, ok, detail in results:
        lines.append(f"| {name} | {'pass' if ok else '**FAIL**'} | {detail} |")
    with open(path, "a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n\n")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", default="http://localhost:8081")
    parser.add_argument("--expect-color", default="")
    parser.add_argument("--expect-version", default="")
    parser.add_argument("--username", default=os.getenv("SMOKE_ADMIN_USERNAME", "admin"))
    args = parser.parse_args(argv)

    password = os.getenv("SMOKE_ADMIN_PASSWORD")
    if not password:
        print("SMOKE_ADMIN_PASSWORD is not set.")
        return 2

    client = Client(args.base_url)
    results: List[Tuple[str, bool, str]] = []
    for name, check in build_checks(client, args.expect_color, args.expect_version,
                                    args.username, password):
        try:
            detail = check()
            results.append((name, True, detail))
            print(f"PASS  {name}: {detail}", flush=True)
        except (CheckFailed, KeyError, ValueError) as error:
            results.append((name, False, str(error)))
            print(f"FAIL  {name}: {error}", flush=True)

    write_summary(results, args.base_url)
    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} smoke checks passed.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
