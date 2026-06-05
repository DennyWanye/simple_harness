# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""首启模型自动下载（thin NSIS bundle 配套 — Option A / 2026-06-05）。

装机包不再内嵌 ~2.7GB 模型（PyInstaller spec `DESKPET_BUNDLE_MODELS=0`），
所以首次启动时 `user_models_dir()` 是空的。本模块在 backend 启动后用一个后台
线程检查缺哪个模型，缺则从 **hf-mirror**（国内镜像）下载，进度经 control-WS
`model_provision_status` 暴露给前端首启进度卡片。

下载等价于 `scripts/{download_bge_m3,download_faster_whisper}.py` 的 CLI 版，
但**目标目录走 `paths.user_models_dir()`**（尊重 `DESKPET_MODEL_ROOT` 等 override），
与运行时 `paths.resolve_model_dir` 实际读取的路径严格一致——避免脚本里硬编码
platformdirs 造成的跨层目录漂移（dev 下两者会不一致）。

未就绪期间 ASR / 记忆走各自的优雅降级（embedder mock、whisper 懒下载），
不阻塞 backend 启动。
"""
from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import structlog

from paths import user_models_dir

logger = structlog.get_logger(__name__)

#: 国内社区镜像；与 download 脚本的 MIRRORS["hf-mirror"] 一致。
HF_MIRROR = "https://hf-mirror.com"

#: (子目录, HF repo_id, 就绪哨兵文件, 预估字节数)。子目录 / repo 与
#: ``scripts/download_*.py`` 的 SUBDIR / REPO_ID 对齐（canonical 在那两个脚本）。
#: 哨兵文件存在即视为该模型已就绪；None 表示"目录非空即就绪"。
#: 预估字节仅用于进度百分比展示，不要求精确。
_MODELS: tuple[tuple[str, str, Optional[str], int], ...] = (
    ("bge-m3-int8", "BAAI/bge-m3", None, 2_300_000_000),
    ("faster-whisper-large-v3-turbo", "mobiuslabsgmbh/faster-whisper-large-v3-turbo", "model.bin", 1_600_000_000),
)


@dataclass
class ProvisionStatus:
    """供前端渲染的快照。state 机：idle→checking→downloading→ready/error。"""

    state: str = "idle"
    current: Optional[str] = None  # 正在下载的模型子目录名
    index: int = 0                 # 1-based：当前是第几个
    total: int = 0                 # 本次需下载的模型总数
    downloaded_bytes: int = 0      # 当前模型已下载（按磁盘实时大小估）
    total_bytes: int = 0           # 当前模型预估总字节（0=未知）
    error: Optional[str] = None

    def as_dict(self) -> dict:
        return {
            "state": self.state,
            "current": self.current,
            "index": self.index,
            "total": self.total,
            "downloaded_bytes": self.downloaded_bytes,
            "total_bytes": self.total_bytes,
            "error": self.error,
        }


def _dir_size(path: Path) -> int:
    """目录下所有文件字节数之和（best-effort，下载中目录在变所以忽略错误）。"""
    total = 0
    try:
        for p in path.rglob("*"):
            try:
                if p.is_file():
                    total += p.stat().st_size
            except OSError:
                continue
    except OSError:
        pass
    return total


# snapshot_download 的注入点签名：``(repo_id, target_dir) -> None``。
Downloader = Callable[[str, Path], None]


class ModelProvisioner:
    """检查并（必要时）下载首启模型。线程安全的状态快照供 IPC 读取。"""

    def __init__(
        self,
        models_dir: Optional[Path] = None,
        mirror: Optional[str] = HF_MIRROR,
        downloader: Optional[Downloader] = None,
    ) -> None:
        # models_dir=None → 运行时解析（尊重 env override）。测试可注入临时目录。
        self._models_dir_override = Path(models_dir) if models_dir else None
        self._mirror = mirror
        # downloader 可注入以便单测不真的联网；默认走 huggingface_hub。
        self._download: Downloader = downloader or self._hf_download
        self._status = ProvisionStatus()
        self._lock = threading.Lock()
        self._started = False

    def __deepcopy__(self, memo):
        # 进程单例：ServiceContext.create_session() 会 deepcopy 整个 context，
        # 而本对象持有不可 deepcopy 的 threading.Lock + 后台线程。返回自身，
        # 让所有 per-session context 共享同一个 provisioner（语义也正确）。
        return self

    # ---- 路径 ----------------------------------------------------------
    def _models_dir(self) -> Path:
        return self._models_dir_override if self._models_dir_override is not None else user_models_dir()

    def _target(self, subdir: str) -> Path:
        return self._models_dir() / subdir

    def _is_ready(self, subdir: str, sentinel: Optional[str]) -> bool:
        d = self._target(subdir)
        if not d.is_dir():
            return False
        if sentinel:
            return (d / sentinel).is_file()
        try:
            return any(d.iterdir())
        except OSError:
            return False

    def missing(self) -> list[tuple[str, str, Optional[str], int]]:
        return [m for m in _MODELS if not self._is_ready(m[0], m[2])]

    # ---- 状态 ----------------------------------------------------------
    def status(self) -> dict:
        with self._lock:
            snap = self._status
            # 下载中时按磁盘实时大小估算 downloaded_bytes（按需计算，无需轮询线程）。
            if snap.state == "downloading" and snap.current:
                snap_dict = snap.as_dict()
                snap_dict["downloaded_bytes"] = _dir_size(self._target(snap.current))
                return snap_dict
            return snap.as_dict()

    def _update(self, **kw) -> None:
        with self._lock:
            for k, v in kw.items():
                setattr(self._status, k, v)

    # ---- 下载 ----------------------------------------------------------
    def _hf_download(self, repo_id: str, target: Path) -> None:
        from huggingface_hub import snapshot_download  # lazy：随 transformers 传递引入

        snapshot_download(
            repo_id=repo_id,
            local_dir=str(target),
            local_dir_use_symlinks=False,
        )

    def _run(self) -> None:
        try:
            self._update(state="checking", error=None)
            missing = self.missing()
            if not missing:
                self._update(state="ready", total=0, current=None)
                logger.info("model_provision_already_ready")
                return

            if self._mirror:
                # 进程级镜像设置（其它 HF 调用也受益）；尊重用户已设的 override。
                os.environ.setdefault("HF_ENDPOINT", self._mirror)

            self._update(state="downloading", total=len(missing))
            logger.info("model_provision_start", count=len(missing), mirror=self._mirror)

            for i, (subdir, repo_id, _sentinel, est) in enumerate(missing, start=1):
                self._update(current=subdir, index=i, downloaded_bytes=0, total_bytes=est)
                target = self._target(subdir)
                target.mkdir(parents=True, exist_ok=True)
                logger.info("model_provision_downloading", model=subdir, repo=repo_id, index=i, total=len(missing))
                self._download(repo_id, target)
                logger.info("model_provision_model_done", model=subdir)

            self._update(state="ready", current=None)
            logger.info("model_provision_done")
        except Exception as exc:  # noqa: BLE001 — 任何失败都转成 error 状态，不崩 backend
            logger.warning("model_provision_failed", error=str(exc), error_type=type(exc).__name__)
            self._update(state="error", error=str(exc))

    def start_background(self) -> None:
        """幂等：起一个 daemon 线程跑 `_run`。已起过则忽略。"""
        with self._lock:
            if self._started:
                return
            self._started = True
        threading.Thread(target=self._run, name="model-provisioner", daemon=True).start()
