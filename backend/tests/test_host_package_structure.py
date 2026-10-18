# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""host 包的结构约束（2026-10-13 代码质量整治）。

主入口拆进 ``backend/host/`` 时，原来在主入口里写的 ``globals().get("X")`` 搬进
host 模块后查的是新模块自己的全局，X 在那里不存在就静默得到 None。语音运行时、
能力运行时、上下文投影都因此丢过依赖。host 模块一律显式导入依赖。
"""

from __future__ import annotations

import ast
from pathlib import Path

HOST = Path(__file__).resolve().parents[1] / "host"


def test_host_modules_do_not_look_up_names_through_globals() -> None:
    offenders = []
    for path in sorted(HOST.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "globals"
            ):
                offenders.append(f"{path.name}:{node.lineno}")
    assert offenders == []


def test_importing_main_writes_nothing_to_stdout() -> None:
    """后端标准输出交给外壳读共享密钥；导入主入口不许往标准输出写任何东西。

    2026-10-13：搬家时删掉了主入口对日志配置模块的导入，日志配置晚于第一条日志，
    导入期日志全部写到了标准输出。
    """
    import os
    import subprocess
    import sys

    backend = HOST.parent
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(x for x in (str(backend), env.get("PYTHONPATH", "")) if x)
    child = subprocess.run(
        [sys.executable, "-c", "import main"],
        cwd=backend,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert child.returncode == 0, child.stderr[-2000:]
    assert child.stdout == ""


def test_main_still_imports_when_the_state_db_cannot_open() -> None:
    """状态库打不开时应用降级启动（会话库为空），主入口仍要能导入。

    2026-10-13：拆主入口后，启动步骤在导入期按名字导入一个只在会话库可用时才定义的名字，
    状态库失败时整个主入口导入失败，后端在握手前就退出。
    """
    import os
    import subprocess
    import sys

    backend = HOST.parent
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(x for x in (str(backend), env.get("PYTHONPATH", "")) if x)
    code = (
        "import deskpet.memory.session_db as m\n"
        "def _boom(*a, **k):\n"
        "    raise RuntimeError('state db unavailable')\n"
        "m.SessionDB = _boom\n"
        "import main\n"
        "from host.stores_bootstrap import _session_db\n"
        "assert _session_db is None\n"
    )
    child = subprocess.run(
        [sys.executable, "-c", code],
        cwd=backend,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert child.returncode == 0, child.stderr[-2000:]
