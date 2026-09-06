"""One fresh interpreter must actually exit; parent pytest cannot close its DBs."""
import json
import os
from pathlib import Path
import subprocess
import sys

import simple_harness


def test_actual_main_execution_exits_without_global_lane_teardown(tmp_path):
    host = Path(__file__).resolve().parents[3]
    target = Path(simple_harness.__file__).resolve().parent.parent
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1",
        PYTHONPATH=os.pathsep.join((str(target), str(host / "backend"))))
    directory = tmp_path / "lifecycle-control"
    child = Path(__file__).with_name("scoring_exit_child.py")
    with (tmp_path / "child.log").open("xb") as log:
        completed = subprocess.run([sys.executable, str(child), str(directory), str(host)],
            env=env, stdout=log, stderr=subprocess.STDOUT, timeout=90, check=False)
    assert completed.returncode == 0, "see isolated child.log; no exit coercion is accepted"
    result = json.loads((directory / "execution.json").read_text())
    assert result["execution_status"] == "COMPLETED" and result["cleanup_errors"] == []
    threads = json.loads((directory / "thread-exit-inventory.json").read_text())
    assert not [t for t in threads if not t["daemon"] and t["alive"] and t["name"] != "MainThread"]
