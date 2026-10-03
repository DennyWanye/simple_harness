# SPDX-License-Identifier: Apache-2.0
"""阶段 C3 的守卫：评测晋级与规则截断摘要两条旧路已删，新库只有全库做法两张表。"""
from __future__ import annotations

from pathlib import Path

import agent_orchestrator
from agent_orchestrator.storage.schema import MIGRATIONS
from agent_orchestrator.storage.store import Store

GONE = ("method_evaluation", "method_lifecycle", "memory.summaries", "memory import summaries",
        "context.compression", "context import compression", "allow_evaluation_trials")


def test_old_paths_are_gone(tmp_path):
    root = Path(agent_orchestrator.__file__).parent
    offenders = [f"{path.relative_to(root)}: {line.strip()}"
                 for path in root.rglob("*.py") for line in path.read_text().splitlines()
                 if (line.lstrip().startswith(("import ", "from ")) or "allow_evaluation_trials" in line)
                 and any(needle in line for needle in GONE)]
    assert offenders == []
    store = Store.open(tmp_path / "orchestrator.db")
    try:
        tables = {row[0] for row in store.connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        store.close()
    assert not {"method_evaluations", "summaries"} & tables
    assert {"method_library", "method_library_attributions"} <= tables
    # migration 23's text is kept byte for byte (moved into schema.py)
    [v23] = [item for item in MIGRATIONS if item.version == 23]
    assert v23.checksum == V23_SHA256


V23_SHA256 = "846c1be84e4d07503f1a2ea3ad56ec2511b4b973255c4f378530b38bb95ddaf9"
