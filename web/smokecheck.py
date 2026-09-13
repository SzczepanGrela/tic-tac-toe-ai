from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from collections.abc import Mapping


REVISION_PATTERN = re.compile(r"^[0-9a-f]{40}$")
DEFAULT_BASE_URL = "http://127.0.0.1:8080"
DEFAULT_TIMEOUT_SECONDS = 10


def _url(base_url: str, path: str) -> str:
    return f"{base_url.rstrip('/')}{path}"


def _open(request: urllib.request.Request | str, timeout: int):
    return urllib.request.urlopen(request, timeout=timeout)


def _read_json(
    request: urllib.request.Request | str,
    timeout: int,
) -> Mapping[str, object]:
    with _open(request, timeout) as response:
        if response.status != 200:
            raise ValueError(f"request returned HTTP {response.status}")
        payload = json.load(response)
    if not isinstance(payload, Mapping):
        raise ValueError("response is not a JSON object")
    return payload


def check_release(
    base_url: str,
    expected_revision: str,
    *,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> None:
    parsed = urllib.parse.urlsplit(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("base URL must be an absolute HTTP or HTTPS URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError(
            "base URL must not contain credentials, a query, or a fragment"
        )
    if not REVISION_PATTERN.fullmatch(expected_revision):
        raise ValueError("expected revision must be a full lowercase commit SHA")

    health_request = urllib.request.Request(
        _url(base_url, "/api/health"),
        headers={
            "Cache-Control": "no-cache",
            "User-Agent": "tic-tac-toe-release-smokecheck",
        },
    )
    health = _read_json(health_request, timeout)
    if health.get("status") != "ok":
        raise ValueError("health status is not ok")
    if health.get("revision") != expected_revision:
        raise ValueError("health revision does not match the expected release")
    agents = health.get("agents")
    if not isinstance(agents, Mapping) or not agents:
        raise ValueError("health response has no agent state")
    if any(state != "ready" for state in agents.values()):
        raise ValueError("at least one agent is not ready")

    for path in ("/", "/static/favicon.svg"):
        request = urllib.request.Request(
            _url(base_url, path),
            headers={"User-Agent": "tic-tac-toe-release-smokecheck"},
        )
        with _open(request, timeout) as response:
            if response.status != 200 or not response.read(1):
                raise ValueError(f"{path} is not available")

    move_request = urllib.request.Request(
        _url(base_url, "/api/move"),
        data=json.dumps(
            {
                "board": [[0, 0, 0], [0, 0, 0], [0, 0, 0]],
                "algorithm": "rules",
                "seed": 1,
            }
        ).encode(),
        headers={
            "Content-Type": "application/json",
            "User-Agent": "tic-tac-toe-release-smokecheck",
        },
        method="POST",
    )
    move = _read_json(move_request, timeout)
    if not isinstance(move.get("move"), Mapping):
        raise ValueError("move endpoint returned no move")


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a deployed release.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument(
        "--expected-revision",
        default=os.getenv("RELEASE_REVISION", ""),
    )
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    args = parser.parse_args()
    try:
        check_release(args.base_url, args.expected_revision, timeout=args.timeout)
    except (OSError, ValueError) as exc:
        print(f"smoke test failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
