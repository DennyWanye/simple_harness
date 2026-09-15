"""Write FREEZE.json: SHA-256 of the frozen taskset + runner + adapter scripts, SDK/Host commits, AppWorld version.
Run once after the 96 episodes are complete; re-run to verify (prints MATCH / MISMATCH)."""
import hashlib, json, subprocess, sys, time
from pathlib import Path
HERE = Path(__file__).resolve().parent
HOST = Path("/Users/taiwan/PROJECTS/SimplaHarness/simple_harness"); SDK = Path("/Users/taiwan/PROJECTS/SimplaHarness/simple-harness-sdk")
FILES = {
    "taskset": HOST / ".local-test-evidence/2026-09-15/a96/appworld-taskset.json",
    "runner": HERE / "run-a96-grok46.py",
    "summarize": HERE / "summarize.py",
    "n7_analysis": HERE / "n7-paired-analysis.py",
    "identity_v2": HERE / "matrix-grok46-256k-v2/identity.json",
}
def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def head(repo): return subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
rec = {
    "frozen_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    "purpose": "N4 Grok baseline (pre-HTN half of the future paired comparison); taskset and runner must not change",
    "files": {k: {"path": str(v), "sha256": sha(v)} for k, v in FILES.items()},
    "sdk_commit": head(SDK), "host_commit": head(HOST),
    "appworld_version": "0.1.3.post1", "model": "grok-4.6", "reasoning_effort": "medium",
    "per_episode_budget": {"total_tokens": 4000000, "calls": 80, "seconds": 1800, "context_window": 262144,
                            "default_output_tokens": 16384, "maximum_output_tokens": 32768, "physical_slots": 1},
    "episodes_expected": 96,
}
target = HERE / "FREEZE.json"
if target.exists() and "--verify" in sys.argv:
    old = json.loads(target.read_text())
    ok = all(old["files"][k]["sha256"] == rec["files"][k]["sha256"] for k in FILES)
    print("MATCH" if ok else "MISMATCH"); sys.exit(0 if ok else 1)
target.write_text(json.dumps(rec, indent=2, ensure_ascii=False) + "\n"); print(json.dumps(rec["files"], indent=1)[:600])
