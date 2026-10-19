"""pytest 插件：工作树借用主检出的虚拟环境时，改写已安装 SDK 记录的来源路径。

已安装包的 direct_url.json 指向主检出的 wheel，本检出的 SDK 候选身份检查会报
"installed origin mismatch"。只在测试进程里把来源路径改写到本检出的同名 wheel
（字节相同，候选身份检查仍按哈希核对），生产代码不动。
环境变量 CQ_ORIGIN_REBASE="主检出 vendor 目录|本检出 vendor 目录"，由 run_related.py 设置。
"""

from __future__ import annotations

import importlib.metadata as _md
import os

_REBASE = os.environ.get("CQ_ORIGIN_REBASE")
_PACKAGES = ("simple-harness-sdk",)

if _REBASE:
    _src, _dst = _REBASE.split("|")
    _orig_distribution = _md.distribution

    class _RebasedDistribution:
        def __init__(self, dist):
            self._dist = dist

        def __getattr__(self, name):
            return getattr(self._dist, name)

        def read_text(self, filename):
            text = self._dist.read_text(filename)
            if filename == "direct_url.json" and text:
                return text.replace(_src, _dst)
            return text

    def _distribution(name):
        dist = _orig_distribution(name)
        return _RebasedDistribution(dist) if name in _PACKAGES else dist

    _md.distribution = _distribution
