# 验收证据 —— 真 frozen 产物

**exe**：`F:\deskpet-build\dist\deskpet-backend\deskpet-backend.exe`（thin bundle，
重打包 mtime 2026-06-28 21:15，含 `_apply_frozen_compat` 修复）。

---

## ① worker 级真验 —— 全新 exe 裸跑当 worker（零注入）

与 backend 运行时 spawn worker **完全相同的真实栈**（同 frozen exe、同 `-m
deskpet.memory.embedder_worker` 入口、同 PYZ、真 import FlagEmbedding、真加载
BGE-M3、真 encode），仅手动喂 stdin：

```
[spawned] alive
[ready] is_mock=False device=cpu load=1302.87ms
[encode] shape=(3, 1024) dtype=float32 finite=True norms=[1.0, 1.0, 1.0] 108.79ms
[shutdown] ok
exit=0 ; stderr 无 "No module" / "getsource" / "datasets." 错误信号
```

→ 三层修复全在代码里、无任何注入，真 encode 出归一化 1024 维向量。

## ② 端到端真验 —— 完整 backend 启动自己 spawn worker（probe-embedder.ps1）

`DESKPET_MODEL_ROOT=G:\projects\deskpet\backend\assets` + `scripts\probe-embedder.ps1`
跑真 `deskpet-backend.exe` 32s：

```
INFO:deskpet.memory.embedder:Spawning BGE-M3 subprocess worker (path=...\assets\bge-m3-int8 device=cpu)
INFO:__main__:event='p4_embedder_ready' is_mock=False
INFO:deskpet.memory.embedder:BGE-M3 worker ready in 1304.5ms (device=cpu)
INFO:deskpet.memory.embedder:BGE-M3 subprocess worker ready (path=...\assets\bge-m3-int8, device=cpu, attempt=1)
```

**对比修复前**（历史所有发布版）：
```
WARNING: BGE-M3 worker spawn attempt 1/2 failed: ... No module named 'datasets'
... 重试 2 次后 falling back to mock embedder
```

验收点逐条命中：
- ✅ 日志出现 `BGE-M3 subprocess worker ready`
- ✅ 最终 `is_mock=False`（真 ready，非先乐观 False 再降级）
- ✅ `attempt=1` 一次成功，无重试、无 `falling back to mock`
- ✅ 无 `No module named`
- ✅ 唯一残留 stderr 是 transformers FutureWarning(TRANSFORMERS_CACHE) + tokenizer 使用
  提示 —— 无害正常警告，非错误

## ③ 单测

```
pytest tests/test_frozen_embedder_compat.py tests/test_frozen_worker_dispatch.py
       tests/test_deskpet_embedder.py  → 21 passed
```
（`test_frozen_embedder_compat.py` 锁住：frozen 守卫 no-op / stub 注入 / dunder→
AttributeError / __spec__ 非 None / 训练符号→RuntimeError / 不覆盖既有 / 调用顺序）

---

## 备注：发版未做（等用户确认）

本修复改了 worker 代码并已重打包验证 frozen 产物。**正式发版**（bump 版本 + 重打
NSIS + 签名 + 发 GitHub/COS）属对外不可逆操作，未执行，等用户确认。
