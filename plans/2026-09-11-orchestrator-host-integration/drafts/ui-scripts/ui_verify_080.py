"""Poll the isolated execution library for the chat Run to finish; print evidence."""
import json, sqlite3, sys, time
from pathlib import Path
EV = Path("/Users/taiwan/PROJECTS/SimplaHarness/simple_harness/.local-test-evidence/2026-09-10/native-ui-080")
db = EV / "userdata/data/simple-harness-sdk/execution-v6.sqlite3"
baseline = int(sys.argv[1]); timeout = float(sys.argv[2]) if len(sys.argv) > 2 else 240
deadline = time.time() + timeout
def snapshot():
    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    rows = c.execute("SELECT run_id, state, driver_kind FROM runs ORDER BY rowid DESC LIMIT 3").fetchall()
    n = c.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
    c.close(); return n, rows
while time.time() < deadline:
    n, rows = snapshot()
    if n > baseline and rows and rows[0][1] in ("completed", "failed", "cancelled"):
        break
    time.sleep(2)
n, rows = snapshot()
c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
versions = [r[0] for r in c.execute("SELECT version FROM sdk_schema_migrations ORDER BY version")]
print(json.dumps({"runs_before": baseline, "runs_after": n, "latest": rows, "schema": versions}, ensure_ascii=False))
