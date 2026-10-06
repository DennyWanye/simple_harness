# -*- coding: utf-8 -*-
"""真实模型验收的"测试控制器钩子"（Assurance 原计划 §15：丢回执、换候选都由确定性切点触发，
不让模型自己去制造失败）。

只在隔离后台进程里生效：驱动脚本把本目录放进 ``PYTHONPATH``，并用环境变量
``ASSURANCE_SCENARIO_HOOK`` 指定要装的钩子；产品源码一行不动。

* ``swap_first_report``：第一次尝试交回结果、工作区拍快照之前，把 ``ASSURANCE_HOOK_TARGET``（默认
  report.json）在磁盘上换成事先准备好的错误报告（``ASSURANCE_HOOK_BAD_FILE``）——场景二"错误的草稿"；
  V28"两条线共用一步、其中一条换做法"用同一个钩子，目标换成 summary.md（合计写错的客户摘要）。
* ``lose_first_publish_reply``：第一次发布，连接器真的把文件放到了接收目录，但回执在路上丢了
  （抛传输错误）——场景四"回执丢了，不能重发"。

每次触发追加一行到 ``ASSURANCE_HOOK_LOG``。钩子按模块名挂在导入时（元路径查找器），
不会在沙箱子进程里提前导入编排包。
"""
from __future__ import annotations

import importlib.abc
import importlib.machinery
import json
import os
import sys
import time

HOOK = os.environ.get("ASSURANCE_SCENARIO_HOOK", "")
LOG = os.environ.get("ASSURANCE_HOOK_LOG", "")
TARGET = os.environ.get("ASSURANCE_HOOK_TARGET", "report.json")


def _log(entry: dict) -> None:
    if not LOG:
        return
    with open(LOG, "a", encoding="utf-8") as handle:
        handle.write(json.dumps({"at": time.time(), "pid": os.getpid(), **entry}, ensure_ascii=False) + "\n")


def _patch_workspace(module) -> None:
    """第一次尝试交回结果、系统给工作区拍快照（逐文件算哈希入库）之前，把目标文件在磁盘上换成错误报告：
    不管执行者是用写文件工具还是在沙箱里跑脚本写出来的，交回去的都是这份错误草稿（哈希、检查、审阅
    都基于它）；第二次尝试起不再干预。"""
    state = {"done": False}
    real = module.Workspace.snapshot
    bad = open(os.environ["ASSURANCE_HOOK_BAD_FILE"], encoding="utf-8").read()

    def snapshot(self, *args, **kwargs):
        target = self.root / TARGET
        if not state["done"] and str(self.attempt_id).endswith(":attempt-1") and target.is_file():
            state["done"] = True
            original = target.read_bytes()
            target.write_text(bad, encoding="utf-8")
            _log({"hook": HOOK, "attempt_id": str(self.attempt_id), "path": TARGET,
                  "original_bytes": len(original), "swapped": True})
        return real(self, *args, **kwargs)

    module.Workspace.snapshot = snapshot


def _patch_connector(module) -> None:
    from agent_orchestrator.runtime.connectors import ConnectorTransportError

    state = {"lost": False}
    real = module.FilePublishConnector.execute

    def execute(self, *args, **kwargs):
        receipt = real(self, *args, **kwargs)
        if not state["lost"]:
            state["lost"] = True
            _log({"hook": HOOK, "event": "reply-lost", "receipt": getattr(receipt, "receipt_id", None)})
            raise ConnectorTransportError("the service applied the publish; its reply was lost")
        return receipt

    module.FilePublishConnector.execute = execute


def _patch_codec(module) -> None:
    """诊断用：编解码撞到字节上限时，把大小和调用栈记下来（只记录，不改行为）。"""
    import traceback

    for name in ("decode", "canonical"):
        real = getattr(module, name)

        def wrapped(*args, _real=real, _name=name, **kwargs):
            try:
                return _real(*args, **kwargs)
            except module.AssuranceError as error:
                if error.code == "JSON_BYTES_LIMIT":
                    raw = args[0] if args else None
                    size = len(raw.encode("utf-8")) if isinstance(raw, str) else (len(raw) if isinstance(raw, bytes) else None)
                    if size is None and _name == "canonical":
                        try:
                            size = len(module.canonical_json(raw).encode("utf-8"))
                        except Exception:  # noqa: BLE001
                            size = -1
                    _log({"hook": HOOK, "where": _name, "bytes": size, "limit": kwargs.get("limit", module.MAX_BYTES),
                          "stack": traceback.format_stack(limit=14)[:-1]})
                raise

        setattr(module, name, wrapped)


PATCHES = {
    "swap_first_report": ("agent_orchestrator.artifacts.workspace", _patch_workspace),
    "lose_first_publish_reply": ("agent_orchestrator.runtime.connectors_publish", _patch_connector),
    "diag_json_limit": ("agent_orchestrator.assurance.codec", _patch_codec),
}


class _Finder(importlib.abc.MetaPathFinder):
    def __init__(self, name, patch):
        self.name, self.patch, self.busy = name, patch, False

    def find_spec(self, fullname, path, target=None):
        if fullname != self.name or self.busy:
            return None
        self.busy = True
        try:
            spec = importlib.util.find_spec(fullname)
        finally:
            self.busy = False
        if spec is None or spec.loader is None:
            return None
        real_exec = spec.loader.exec_module
        patch = self.patch

        def exec_module(module):
            real_exec(module)
            patch(module)
            _log({"hook": HOOK, "event": "installed", "module": fullname})

        spec.loader.exec_module = exec_module  # type: ignore[method-assign]
        return spec


if HOOK in PATCHES:
    import importlib.util

    sys.meta_path.insert(0, _Finder(*PATCHES[HOOK]))
