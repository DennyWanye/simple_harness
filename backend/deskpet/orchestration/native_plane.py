# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Host 这一侧的原生执行池资源（2026-10-03 起，拼装本身在 SDK ``agent_orchestrator.deployment.native_pools``）。

Host 只提供本机资源：BGE-M3 向量模型（没装就只按词检索，如实报告原因）、已证明可用的沙箱脚本
执行器（没有就按名字拒绝 SCRIPT 技能）、用量计数器。执行池的身份与拼装只有 SDK 那一份。
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from agent_orchestrator.deployment.native_pools import NativePools

from .skill_script_runner import SandboxScriptRunner

logger = logging.getLogger(__name__)

BGE_M3_SUBDIR = "bge-m3-int8"


BGE_M3_MODEL_FILE = "model.int8.onnx"
BGE_M3_DIM = 1024
BGE_M3_MAX_TOKENS = 8192


class BgeM3EmbeddingPort:
    """BGE-M3 dense vectors from the local INT8 ONNX model (user decision 2026-09-24).

    The model is BAAI/bge-m3's official ONNX export, dynamically quantised to INT8 on this
    machine (542 MB; cosine 0.99 to the full-precision model, identical rankings in the
    acceptance probe), saved with its weights in ``model.int8.onnx.data`` so onnxruntime
    maps them instead of copying the protobuf (resident ~0.7 GB instead of ~1.6 GB).  It runs on ``onnxruntime`` with the model's own ``tokenizers``
    tokenizer: no torch, no FlagEmbedding.  The model's ``sentence_embedding`` output is
    the CLS vector, L2-normalised.

    The fingerprint is computed from the model directory at assembly (configuration and
    tokenizer bytes plus every file name and size); the session loads on the first call.
    A failing load is recorded by the SDK as a FAILED embedding receipt and the session
    degrades to lexical retrieval by the profile's rule; nothing is guessed.
    """

    pricing_mode = "NO_PROVIDER_CHARGE"
    # A real model (0.2–2 s per batch): the SDK computes index vectors in its background
    # pump off the event loop, never inside a Turn's prepare.
    prefers_background = True

    def __init__(self, model_dir: Path, *, max_tokens: int = BGE_M3_MAX_TOKENS) -> None:
        self.model_dir = Path(model_dir)
        self.max_tokens = int(max_tokens)
        body = hashlib.sha256()
        for path in sorted(self.model_dir.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(self.model_dir).as_posix()
            body.update(relative.encode("utf-8") + b"\0")
            if path.name in ("config.json", "tokenizer.json", "tokenizer_config.json"):
                body.update(path.read_bytes())
            body.update(str(path.stat().st_size).encode("ascii") + b"\0")
        body.update(f"max_tokens={self.max_tokens}".encode("ascii"))
        self.fingerprint = "bge-m3-int8-onnx:" + body.hexdigest()
        self._session: Any = None
        self._tokenizer: Any = None
        self._load_error: Exception | None = None

    @property
    def dim(self) -> int:
        return BGE_M3_DIM

    def _load(self) -> None:
        import onnxruntime as ort
        from tokenizers import Tokenizer

        options = ort.SessionOptions()
        options.enable_cpu_mem_arena = False  # a desktop app: return buffers after each batch
        self._session = ort.InferenceSession(
            str(self.model_dir / BGE_M3_MODEL_FILE), sess_options=options, providers=["CPUExecutionProvider"],
        )
        tokenizer = Tokenizer.from_file(str(self.model_dir / "tokenizer.json"))
        tokenizer.enable_truncation(max_length=self.max_tokens)
        tokenizer.enable_padding(pad_id=1, pad_token="<pad>")
        self._tokenizer = tokenizer

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if self._load_error is not None:
            raise self._load_error  # a failed load stays failed for this lifetime: no retry storm
        if self._session is None:
            try:
                self._load()
            except Exception as error:  # noqa: BLE001 - recorded by the SDK as a FAILED receipt
                self._load_error = error
                logger.warning("BGE-M3 load failed; native sessions degrade to lexical retrieval: %s", type(error).__name__)
                raise
        import numpy as np

        vectors: list[list[float]] = []
        items = list(texts)
        for start in range(0, len(items), 8):
            encoded = self._tokenizer.encode_batch(items[start : start + 8])
            ids = np.array([e.ids for e in encoded], dtype=np.int64)
            mask = np.array([e.attention_mask for e in encoded], dtype=np.int64)
            dense = self._session.run(["sentence_embedding"], {"input_ids": ids, "attention_mask": mask})[0]
            norms = np.linalg.norm(dense, axis=1, keepdims=True)
            dense = dense / np.where(norms == 0, 1.0, norms)
            vectors.extend([float(x) for x in row] for row in dense)
        return vectors


def embedding_port(models_dir: Path | None) -> tuple[BgeM3EmbeddingPort | None, str | None]:
    """The BGE-M3 INT8 port when the model is installed, else ``(None, reason)``."""

    if models_dir is None:
        return None, "模型目录未配置"
    model_dir = Path(models_dir) / BGE_M3_SUBDIR
    for name in (BGE_M3_MODEL_FILE, BGE_M3_MODEL_FILE + ".data", "tokenizer.json"):
        if not (model_dir / name).is_file():
            return None, f"BGE-M3 INT8 模型不完整：缺 {model_dir / name}"
    try:
        import importlib.util

        for module in ("onnxruntime", "tokenizers", "numpy"):
            if importlib.util.find_spec(module) is None:
                return None, f"{module} 未安装"
    except Exception:  # noqa: BLE001 - a broken import system is a missing resource
        return None, "ONNX 运行库不可用"
    return BgeM3EmbeddingPort(model_dir), None


def build_native_pools(
    *,
    tenant_id: str,
    principal_id: str,
    allowed_tools: Sequence[str],
    models_dir: Path | None,
    meter_factory: Callable[..., Any],
    script_executor: Any | None = None,
    clock_ms: Callable[[], int] | None = None,
) -> NativePools:
    """The SDK's native pools over this machine's resources."""

    embedding, reason = embedding_port(models_dir)
    extra = {} if clock_ms is None else {"clock_ms": clock_ms}
    return NativePools(
        tenant_id=tenant_id, principal_id=principal_id, allowed_tools=allowed_tools,
        meter_factory=meter_factory, embedding=embedding, embedding_reason=reason,
        # The sandbox executor this deployment proved at start (P3.2 probe); None when the
        # machine has no usable sandbox, and then SCRIPT skills are refused by name.
        script_runner=None if script_executor is None else (
            lambda runs_dir: SandboxScriptRunner(script_executor, runs_dir)),
        script_runner_label=None if script_executor is None else f"sandbox:{script_executor.kind}",
        **extra,
    )


__all__ = ("BgeM3EmbeddingPort", "build_native_pools", "embedding_port")
