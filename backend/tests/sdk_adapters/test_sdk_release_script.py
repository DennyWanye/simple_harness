"""Keep the release entry point bound to runnable Host acceptance tests."""

import re
from pathlib import Path


def test_release_host_selectors_exist_and_include_the_current_session_seam():
    repo = Path(__file__).resolve().parents[3]
    script = (repo / "scripts/release_sdk_opt.sh").read_text()
    selectors = re.findall(r"\btests/[A-Za-z0-9_./]+\.py", script)
    missing = [name for name in selectors if not (repo / "backend" / name).is_file()]
    assert not missing, f"Release cannot collect its Host tests: {missing}"
    assert "tests/orchestration/test_desktop_session_contract.py" in selectors
    assert "tests/orchestration/test_session_bridge.py" in selectors
