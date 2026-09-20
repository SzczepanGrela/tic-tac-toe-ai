"""Measure two simultaneous MCTS moves and health on a local running container."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import threading
import time
from urllib.parse import urlparse
from urllib.request import Request, urlopen


def measure(base: str) -> dict:
    if urlparse(base).hostname not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError("This benchmark accepts only a local test server")
    barrier = threading.Barrier(3)
    def move(seed):
        payload = {"board": [[0] * 5 for _ in range(5)], "board_size": 5,
                   "win_length": 4, "algorithm": "mcts", "seed": seed}
        barrier.wait()
        started = time.monotonic()
        request = Request(base + "/api/move", data=json.dumps(payload).encode(),
                          headers={"Content-Type": "application/json"})
        with urlopen(request, timeout=7) as response:
            result = json.load(response)
        assert 0 <= result["move"]["row"] < 5 and 0 <= result["move"]["column"] < 5
        return time.monotonic() - started
    health = []
    with ThreadPoolExecutor(2) as pool:
        jobs = [pool.submit(move, seed) for seed in (42, 43)]
        barrier.wait()
        while not all(job.done() for job in jobs) or not health:
            started = time.monotonic()
            with urlopen(base + "/api/health", timeout=2) as response:
                assert json.load(response)["status"] == "ok"
            health.append(time.monotonic() - started)
            time.sleep(.005)
        moves = [job.result() for job in jobs]
    return {"concurrent_move_seconds": moves, "health_samples": len(health),
            "max_health_seconds": max(health)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    args = parser.parse_args()
    print(json.dumps(measure(args.base_url.rstrip("/"))))
