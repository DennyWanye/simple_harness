# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""单测：frozen embedder worker 的 datasets stub + frozen 守卫。

真二进制三层修复（datasets / inspect.getsource / transformers.models.*）的
端到端证据在 plans/2026-06-28-frozen-embedder-datasets-fix/ 的真 exe 跑测里；
这里只锁住 dev/source 与 frozen 的分支语义 + stub 行为，避免回归时悄悄退化。
"""
import sys

import pytest

from deskpet.memory import embedder_worker as ew


@pytest.fixture
def _no_datasets(monkeypatch):
    """临时把 sys.modules['datasets'] 移除并在测试后恢复，避免污染真包。"""
    saved = sys.modules.pop("datasets", None)
    yield
    if saved is not None:
        sys.modules["datasets"] = saved
    else:
        sys.modules.pop("datasets", None)


def test_apply_frozen_compat_is_noop_when_not_frozen(monkeypatch, _no_datasets):
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    ew._apply_frozen_compat()
    # 非 frozen → 不应注入 stub
    assert "datasets" not in sys.modules


def test_install_datasets_stub_injects_minimal_module(_no_datasets):
    injected = ew._install_datasets_stub()
    assert injected is True
    stub = sys.modules["datasets"]
    # 唯一加载期引用 datasets.Dataset 必须可取（AbsDataset 函数注解）
    assert isinstance(stub.Dataset, type)
    assert "stub" in stub.__version__
    # __spec__ 必须是真 ModuleSpec（非 None）—— 否则 importlib.util.find_spec
    # （transformers `_is_package_available` 调用）会对 None 抛 ValueError。
    assert stub.__spec__ is not None
    # find_spec 命中 sys.modules 的 stub 应正常返回（不抛）
    import importlib.util

    assert importlib.util.find_spec("datasets") is not None


def test_datasets_stub_dunder_access_raises_attributeerror(_no_datasets):
    """dunder（__file__ 等）必须抛 AttributeError，让 repr/importlib/inspect
    的内省走 getattr-default / try-except 优雅降级，而非被 RuntimeError 打挂。"""
    ew._install_datasets_stub()
    stub = sys.modules["datasets"]
    # repr 内部读 __file__ —— 不能炸
    assert "datasets" in repr(stub)
    assert getattr(stub, "__file__", "fallback") == "fallback"
    with pytest.raises(AttributeError):
        _ = stub.__path__  # noqa: B018


def test_datasets_stub_raises_on_training_symbols(_no_datasets):
    ew._install_datasets_stub()
    stub = sys.modules["datasets"]
    # 训练期符号被真用到才报错（推理永不触达）
    with pytest.raises(RuntimeError, match="stub"):
        _ = stub.load_dataset
    with pytest.raises(RuntimeError, match="stub"):
        _ = stub.concatenate_datasets


def test_install_datasets_stub_does_not_clobber_existing():
    sentinel = object()
    saved = sys.modules.get("datasets")
    sys.modules["datasets"] = sentinel  # type: ignore[assignment]
    try:
        injected = ew._install_datasets_stub()
        assert injected is False
        assert sys.modules["datasets"] is sentinel
    finally:
        if saved is not None:
            sys.modules["datasets"] = saved
        else:
            sys.modules.pop("datasets", None)


def test_load_model_calls_frozen_compat_before_flagembedding(monkeypatch):
    """_load_model 必须在 import FlagEmbedding 之前调用 _apply_frozen_compat。"""
    import inspect

    src = inspect.getsource(ew._load_model)
    compat_pos = src.index("_apply_frozen_compat()")
    import_pos = src.index("from FlagEmbedding import")
    assert compat_pos < import_pos
