from __future__ import annotations

import json
import sys
import urllib.request
from collections.abc import Mapping


HEALTH_URL = "http://127.0.0.1:8080/api/health"
HEALTH_TIMEOUT_SECONDS = 3


def check_health() -> None:
    request = urllib.request.Request(
        HEALTH_URL,
        headers={"User-Agent": "tic-tac-toe-healthcheck"},
    )
    with urllib.request.urlopen(request, timeout=HEALTH_TIMEOUT_SECONDS) as response:
        if response.status != 200:
            raise ValueError(f"health endpoint returned HTTP {response.status}")
        payload = json.load(response)

    if not isinstance(payload, Mapping):
        raise ValueError("health response is not an object")
    if payload.get("status") != "ok":
        raise ValueError("health status is not ok")

    revision = payload.get("revision")
    if not isinstance(revision, str) or not revision:
        raise ValueError("health response has no revision")

    agents = payload.get("agents")
    if not isinstance(agents, Mapping) or not agents:
        raise ValueError("health response has no agent state")
    if any(state != "ready" for state in agents.values()):
        raise ValueError("at least one agent is not ready")


def main() -> int:
    try:
        check_health()
    except Exception as exc:
        print(f"healthcheck failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
