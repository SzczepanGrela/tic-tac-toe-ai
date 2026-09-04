from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.request

import pytest


@pytest.fixture(scope="session")
def live_server_url():
    external_url = os.getenv("E2E_BASE_URL")
    if external_url:
        yield external_url.rstrip("/")
        return

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "web.app:app", "--host", "127.0.0.1", "--port", str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    url = f"http://127.0.0.1:{port}"
    try:
        for _ in range(50):
            try:
                urllib.request.urlopen(f"{url}/api/health", timeout=1)
                break
            except OSError:
                time.sleep(0.1)
        else:
            raise RuntimeError("test web server did not start")
        yield url
    finally:
        process.terminate()
        process.wait(timeout=10)
