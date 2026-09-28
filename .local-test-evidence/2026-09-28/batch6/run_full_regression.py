"""第六批全量回归：SDK 与 Host 按目录分进程跑（每份有硬时限），前端整套 vitest + 类型检查。

用法：python run_full_regression.py <out_dir> [sdk|host|frontend ...]
输出：<out_dir>/<scope>/<part>.log、summary.tsv、failed-nodeids.txt
"""
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path("/Users/taiwan/PROJECTS/SimplaHarness/simple_harness")
SDK = ROOT / "sdk/simple-harness-sdk"
HOST = ROOT / "backend"
APP = ROOT / "tauri-app"
LIMIT = 1800
out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
scopes = sys.argv[2:] or ["sdk", "host", "frontend"]
PYTEST = ["-q", "-p", "no:cacheprovider", "-rfE", "-o", "faulthandler_timeout=240", "--continue-on-collection-errors"]


def run(scope: str, name: str, args: list[str], cwd: Path, env: dict | None = None) -> tuple[str, str]:
    folder = out / scope; folder.mkdir(exist_ok=True)
    log = folder / f"{name}.log"
    started = time.time()
    with log.open("w") as handle:
        proc = subprocess.Popen(args, cwd=cwd, stdout=handle, stderr=subprocess.STDOUT, start_new_session=True,
                                env={**os.environ, **(env or {})})
        try:
            status = f"exit={proc.wait(timeout=LIMIT)}"
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL); proc.wait(); status = "TIMEOUT"
    text = log.read_text(errors="replace")
    tail = [line for line in text.splitlines() if re.search(r"\d+ (passed|failed|error)", line)]
    failed = [line.split(" - ")[0] for line in text.splitlines() if line.startswith(("FAILED ", "ERROR "))]
    with (folder / "summary.tsv").open("a") as summary:
        summary.write(f"{name}\t{status}\t{int(time.time() - started)}s\t{tail[-1] if tail else ''}\n")
    with (folder / "failed-nodeids.txt").open("a") as handle:
        handle.write("".join(f"{f}\n" for f in failed))
    return status, tail[-1] if tail else ""


def sdk() -> None:
    uv = ["uv", "run", "--frozen", "--group", "dev", "--extra", "local-capacity", "pytest", *PYTEST]
    orch = SDK / "tests/orchestrator"
    for part in sorted(p for p in orch.iterdir() if p.is_dir() and p.name not in ("__pycache__", "gap_phase1")):
        run("sdk", f"orchestrator-{part.name}", uv + [str(part.relative_to(SDK))], SDK)
    run("sdk", "orchestrator-root", uv + [str(p.relative_to(SDK)) for p in sorted(orch.glob("test_*.py"))], SDK)
    run("sdk", "orchestrator-gap_phase1",
        ["uv", "run", "--frozen", "--group", "dev", "--extra", "local-capacity", "--with", "pydantic>=2", "--with", "pytest-asyncio",
         "pytest", *PYTEST, "tests/orchestrator/gap_phase1"], SDK)
    for part in sorted(p for p in (SDK / "tests").iterdir() if p.is_dir() and p.name not in ("__pycache__", "orchestrator")):
        run("sdk", part.name, uv + [str(part.relative_to(SDK))], SDK)


def host() -> None:
    uv = ["uv", "run", "--frozen", "python", "-m", "pytest", *PYTEST]
    tests = HOST / "tests"
    for part in sorted(p for p in tests.iterdir() if p.is_dir() and p.name not in ("__pycache__", "fixtures", "scripts")):
        run("host", part.name, uv + [str(part.relative_to(HOST))], HOST)
    files = sorted(tests.glob("test_*.py"))
    for i in range(0, len(files), 60):
        run("host", f"root-{i // 60:02d}", uv + [str(p.relative_to(HOST)) for p in files[i:i + 60]], HOST)


def frontend() -> None:
    run("frontend", "vitest", ["npx", "vitest", "run"], APP)
    run("frontend", "typecheck", ["npx", "tsc", "--noEmit", "-p", "."], APP)


for scope in scopes:
    {"sdk": sdk, "host": host, "frontend": frontend}[scope]()
print("done", flush=True)
