"""Wait until the backend log stops growing (pet idle) before sending the next message.
Log path: env DESKPET_E2E_LOG, else default ship-dir tauri-dev.log.
Usage: python wait_idle.py [stable_s=8] [max_s=120]
"""
from __future__ import annotations

import os
import re
import sys
import time

LOG = os.environ.get(
    "DESKPET_E2E_LOG",
    r"G:\projects\deskpet\plans\manual-results-2026-06-25-problem-pipeline-ship\tauri-dev.log",
)
START = re.compile(r"^(INFO|WARNING|ERROR|DEBUG|CRITICAL):")


def count_logical() -> int:
    try:
        with open(LOG, "r", encoding="utf-16-le", errors="replace") as f:
            raw = f.read().splitlines()
    except Exception:
        return -1
    n = 0
    started = False
    for ln in raw:
        if START.match(ln) or not started:
            n += 1
            started = True
    return n


def main() -> int:
    stable_s = float(sys.argv[1]) if len(sys.argv) > 1 else 8.0
    max_s = float(sys.argv[2]) if len(sys.argv) > 2 else 120.0
    t_start = time.time()
    last = count_logical()
    last_change = time.time()
    while time.time() - t_start < max_s:
        time.sleep(2)
        cur = count_logical()
        if cur != last:
            last = cur
            last_change = time.time()
        elif time.time() - last_change >= stable_s:
            print(f"IDLE TOTAL_LOGICAL={cur} waited={time.time()-t_start:.0f}s")
            return 0
    print(f"TIMEOUT TOTAL_LOGICAL={last} waited={time.time()-t_start:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
