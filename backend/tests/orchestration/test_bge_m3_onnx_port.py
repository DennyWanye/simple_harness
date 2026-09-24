"""BGE-M3 INT8 ONNX 嵌入端口（2026-09-24 用户决定：用 BGE-M3 int8）。

不依赖 FlagEmbedding / torch：onnxruntime + tokenizers 读取本机量化模型（权重在外部数据
文件里，常驻约 0.7 GB）。模型未安装时如实给出原因；本机装了模型时验证维度、归一化与语义排序。
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from deskpet.orchestration.native_plane import BGE_M3_SUBDIR, BgeM3EmbeddingPort, embedding_port

MODELS = Path.home() / "Library/Application Support/com.dennywanye.simpleharness/models"


def test_missing_or_incomplete_models_are_named(tmp_path) -> None:
    assert embedding_port(None) == (None, "模型目录未配置")
    port, reason = embedding_port(tmp_path)
    assert port is None and "model.int8.onnx" in reason
    folder = tmp_path / BGE_M3_SUBDIR
    folder.mkdir()
    (folder / "model.int8.onnx").write_bytes(b"graph")
    port, reason = embedding_port(tmp_path)
    assert port is None and "model.int8.onnx.data" in reason  # the external weights are required too


def test_the_port_declares_background_computation(tmp_path) -> None:
    folder = tmp_path / BGE_M3_SUBDIR
    folder.mkdir()
    for name in ("model.int8.onnx", "model.int8.onnx.data", "tokenizer.json"):
        (folder / name).write_bytes(b"x")
    port, reason = embedding_port(tmp_path)
    assert reason is None and isinstance(port, BgeM3EmbeddingPort)
    assert port.prefers_background is True and port.dim == 1024 and port.fingerprint.startswith("bge-m3-int8-onnx:")


@pytest.mark.skipif(not (MODELS / BGE_M3_SUBDIR / "model.int8.onnx.data").is_file(), reason="BGE-M3 INT8 model not installed")
def test_real_model_vectors_are_normalised_and_semantic() -> None:
    port, reason = embedding_port(MODELS)
    assert reason is None
    docs = ["本工程的暗号是「蓝鲸七号」；所有报告发出前必须先经过李工审阅。", "今天的天气有些闷热，午后可能下雨。",
            "The deployment must be approved by the security team."]
    vectors = port.embed(docs)
    query = port.embed(["工程的暗号是什么", "哪个团队要批准部署？"])
    assert len(vectors) == 3 and all(len(v) == 1024 for v in vectors)
    assert all(abs(math.sqrt(sum(x * x for x in v)) - 1.0) < 1e-3 for v in vectors)

    def best(q):  # type: ignore[no-untyped-def]
        return max(range(3), key=lambda i: sum(a * b for a, b in zip(q, vectors[i])))

    assert best(query[0]) == 0 and best(query[1]) == 2
