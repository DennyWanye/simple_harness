"""强验证：frozen exe worker 输出的向量 vs source venv 真 BGEM3FlagModel 对拍。

若 frozen 输出与真模型逐元素一致(浮点误差级)，且语义相似度合理，则铁证 frozen
跑的是真 BGE-M3，而非 mock 假象。mock(hash 随机)会与真模型完全不同。

跑: F:/deskpet-build/venv/Scripts/python.exe scripts/probe_frozen_vs_real.py
"""
import base64
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

MODEL = "G:/projects/deskpet/backend/assets/bge-m3-int8"
EXE = "F:/deskpet-build/dist/deskpet-backend/deskpet-backend.exe"
DIST = str(Path(EXE).parent)
SENTS = ["今天天气很好", "今天天气晴朗", "股票市场今日大跌"]


def frozen_encode(sents: list[str]) -> np.ndarray:
    """驱动真 frozen exe 当 worker，返回它 encode 出的向量。"""
    proc = subprocess.Popen(
        [EXE, "-X", "utf8", "-m", "deskpet.memory.embedder_worker",
         "--model-path", MODEL, "--device", "cpu"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, cwd=DIST, text=False,
    )
    assert proc.stdin and proc.stdout
    spawn = json.loads(proc.stdout.readline().decode("utf-8"))
    assert spawn.get("stage") == "spawned", spawn
    ready = json.loads(proc.stdout.readline().decode("utf-8"))
    assert ready.get("ready"), f"worker 未 ready(可能 fatal/mock): {ready}"
    req = {"id": 1, "method": "encode", "texts": sents,
           "batch_size": 4, "max_length": 128}
    proc.stdin.write((json.dumps(req, ensure_ascii=False) + "\n").encode("utf-8"))
    proc.stdin.flush()
    resp = json.loads(proc.stdout.readline().decode("utf-8"))
    assert resp.get("ok") and resp.get("encoding") == "base64-f32", resp
    arr = np.frombuffer(base64.b64decode(resp["vectors_b64"]),
                        dtype=np.float32).reshape(resp["shape"]).copy()
    proc.stdin.write(b'{"method":"shutdown"}\n')
    proc.stdin.flush()
    proc.wait(timeout=10)
    return arr


def real_encode(sents: list[str]) -> np.ndarray:
    """source venv 里直接加载真 BGEM3FlagModel(参照系)。"""
    from FlagEmbedding import BGEM3FlagModel
    m = BGEM3FlagModel(MODEL, use_fp16=False, device="cpu")
    out = m.encode(sents, batch_size=4, max_length=128, return_dense=True,
                   return_sparse=False, return_colbert_vecs=False)
    return np.asarray(out["dense_vecs"], dtype=np.float32)


def cos(a, b):
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))


def main() -> int:
    print("=== 1) frozen exe worker 输出 ===")
    fz = frozen_encode(SENTS)
    print(f"    shape={fz.shape}")
    print("=== 2) source venv 真 BGEM3FlagModel 输出(参照系) ===")
    rl = real_encode(SENTS)
    print(f"    shape={rl.shape}")

    print("\n=== 3) 逐元素对拍(frozen vs 真模型) ===")
    max_abs = float(np.max(np.abs(fz - rl)))
    diag_cos = [cos(fz[i], rl[i]) for i in range(len(SENTS))]
    print(f"    max|Δ| = {max_abs:.3e}")
    print(f"    对应行余弦 = {[round(c,6) for c in diag_cos]}")
    same = max_abs < 1e-3 and all(c > 0.9999 for c in diag_cos)
    print(f"    => frozen 输出 {'≡ 真模型(铁证真 BGE-M3,非 mock)' if same else '≠ 真模型(可疑!)'}")

    print("\n=== 4) 语义合理性(真 BGE-M3 应: 相似句高、无关句低) ===")
    c_sim = cos(fz[0], fz[1])   # 今天天气很好 vs 今天天气晴朗
    c_unrel = cos(fz[0], fz[2])  # 今天天气很好 vs 股票大跌
    print(f"    cos(相似句  A,B) = {c_sim:.4f}")
    print(f"    cos(无关句  A,C) = {c_unrel:.4f}")
    semantic_ok = c_sim > c_unrel and c_sim > 0.6
    print(f"    => 语义{'合理' if semantic_ok else '异常(像 mock 随机)'}")

    print("\n" + "=" * 56)
    if same and semantic_ok:
        print("PASS: frozen 产物逐元素等于真 BGE-M3 且语义正确 → 确非 mock")
        return 0
    print("FAIL: 验证未通过")
    return 1


if __name__ == "__main__":
    sys.exit(main())
