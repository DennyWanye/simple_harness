"""一次性：用 Host 当时的代码生成执行池身份基准（搬迁前 8b656b2c 已用同样输入生成并提交）。

用法（backend 目录）：.venv/bin/python tests/orchestration/_gen_pool_identity_baseline.py <输出文件>
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, ".")
sys.path.insert(0, "tests/orchestration")
import _pool_identity as P  # noqa: E402
from _word_counter import FixtureWordCounter  # noqa: E402

out = {}
for models, snapshot in P.VARIANTS:
    with tempfile.TemporaryDirectory() as d:
        out[f"models={models},snapshot={snapshot}"] = P.build(Path(d), models=models, snapshot=snapshot,
                                                              counter_factory=FixtureWordCounter)
Path(sys.argv[1]).write_text(json.dumps(out, ensure_ascii=False, indent=1, sort_keys=True) + "\n")
