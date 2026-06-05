# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Unit tests for first-run model provisioner (Option A / thin bundle).

不联网：注入 fake downloader。验证缺失检测、状态机、镜像设置、错误降级，
以及目标目录走注入的 models_dir（不被 user_models_dir override 影响）。
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from deskpet.model_provisioner import _MODELS, ModelProvisioner


def _populate(models_dir: Path, subdir: str, sentinel: str | None) -> None:
    d = models_dir / subdir
    d.mkdir(parents=True, exist_ok=True)
    (d / (sentinel or "weights.bin")).write_bytes(b"x" * 16)


def test_missing_lists_all_when_empty(tmp_path: Path) -> None:
    prov = ModelProvisioner(models_dir=tmp_path, downloader=lambda r, t: None)
    missing = prov.missing()
    assert {m[0] for m in missing} == {m[0] for m in _MODELS}


def test_missing_excludes_ready_models(tmp_path: Path) -> None:
    # 预置 whisper（含哨兵 model.bin）→ 只剩 bge-m3 缺。
    _populate(tmp_path, "faster-whisper-large-v3-turbo", "model.bin")
    prov = ModelProvisioner(models_dir=tmp_path, downloader=lambda r, t: None)
    missing = [m[0] for m in prov.missing()]
    assert missing == ["bge-m3-int8"]


def test_sentinel_required_for_whisper(tmp_path: Path) -> None:
    # whisper 目录存在但缺 model.bin 哨兵 → 仍算缺失（半下载不算就绪）。
    (tmp_path / "faster-whisper-large-v3-turbo").mkdir(parents=True)
    (tmp_path / "faster-whisper-large-v3-turbo" / "config.json").write_text("{}")
    prov = ModelProvisioner(models_dir=tmp_path, downloader=lambda r, t: None)
    assert "faster-whisper-large-v3-turbo" in {m[0] for m in prov.missing()}


def test_run_downloads_missing_and_reaches_ready(tmp_path: Path) -> None:
    calls: list[tuple[str, Path]] = []

    def fake_dl(repo_id: str, target: Path) -> None:
        calls.append((repo_id, target))
        # 模拟下载：写出哨兵 / 文件，让 _is_ready 之后为真。
        for subdir, repo, sentinel, _est in _MODELS:
            if repo == repo_id:
                _populate(tmp_path, subdir, sentinel)

    prov = ModelProvisioner(models_dir=tmp_path, mirror=None, downloader=fake_dl)
    prov._run()

    assert prov.status()["state"] == "ready"
    assert len(calls) == len(_MODELS)
    # 跑完后再查应无缺失。
    assert prov.missing() == []


def test_run_noop_when_all_ready(tmp_path: Path) -> None:
    for subdir, _repo, sentinel, _est in _MODELS:
        _populate(tmp_path, subdir, sentinel)

    called = False

    def fake_dl(repo_id: str, target: Path) -> None:
        nonlocal called
        called = True

    prov = ModelProvisioner(models_dir=tmp_path, downloader=fake_dl)
    prov._run()
    assert prov.status()["state"] == "ready"
    assert prov.status()["total"] == 0
    assert called is False


def test_run_sets_mirror_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HF_ENDPOINT", raising=False)
    prov = ModelProvisioner(
        models_dir=tmp_path,
        mirror="https://hf-mirror.com",
        downloader=lambda r, t: _populate(tmp_path, _repo_to_subdir(r), _repo_to_sentinel(r)),
    )
    prov._run()
    assert os.environ.get("HF_ENDPOINT") == "https://hf-mirror.com"


def test_run_error_degrades_to_error_state(tmp_path: Path) -> None:
    def boom(repo_id: str, target: Path) -> None:
        raise RuntimeError("network down")

    prov = ModelProvisioner(models_dir=tmp_path, mirror=None, downloader=boom)
    prov._run()
    st = prov.status()
    assert st["state"] == "error"
    assert "network down" in (st["error"] or "")


def test_status_reports_downloaded_bytes_from_disk(tmp_path: Path) -> None:
    # 模拟"下载中"：手动置状态 + 写入部分字节，status() 应按磁盘大小估算。
    prov = ModelProvisioner(models_dir=tmp_path, downloader=lambda r, t: None)
    sub = "bge-m3-int8"
    (tmp_path / sub).mkdir(parents=True)
    (tmp_path / sub / "part.bin").write_bytes(b"y" * 4096)
    prov._update(state="downloading", current=sub, index=1, total=1, total_bytes=2_300_000_000)
    assert prov.status()["downloaded_bytes"] == 4096


def _repo_to_subdir(repo_id: str) -> str:
    return next(m[0] for m in _MODELS if m[1] == repo_id)


def _repo_to_sentinel(repo_id: str) -> str | None:
    return next(m[2] for m in _MODELS if m[1] == repo_id)


# --- control-WS handler 契约（前端 ModelDownloadBanner 依赖字段名）-----------


class _FakeWS:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_json(self, obj: dict) -> None:
        self.sent.append(obj)


def test_ipc_handler_returns_status(tmp_path: Path) -> None:
    import asyncio

    import context as ctx_mod
    import p4_ipc

    prov = ModelProvisioner(models_dir=tmp_path, downloader=lambda r, t: None)
    sc = ctx_mod.ServiceContext()
    sc.register("model_provisioner", prov)
    ws = _FakeWS()
    asyncio.run(p4_ipc._handle_model_provision_status(ws, {}, sc))

    assert len(ws.sent) == 1
    msg = ws.sent[0]
    assert msg["type"] == "model_provision_status_response"
    # 前端契约：payload 含 state；字段名必须稳定。
    assert set(msg["payload"]).issuperset({"state"})
    assert msg["payload"]["state"] in {"idle", "checking", "downloading", "ready", "error"}


def test_ipc_handler_unregistered_returns_ready() -> None:
    import asyncio

    import context as ctx_mod
    import p4_ipc

    sc = ctx_mod.ServiceContext()  # 没注册 provisioner（如非瘦包构建）
    ws = _FakeWS()
    asyncio.run(p4_ipc._handle_model_provision_status(ws, {}, sc))
    assert ws.sent[0]["payload"]["state"] == "ready"
