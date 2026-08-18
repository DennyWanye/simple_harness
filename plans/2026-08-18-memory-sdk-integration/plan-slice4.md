# Plan：记忆 SDK 接入 slice 4 — 真实后端启动 smoke

## 主要矛盾
验证 slice 3 的接线在**真实 backend 运行栈**（`import main` + SessionDB/MemoryBackend 实例）
里真正生效，而不是只测构造模式。

## 关联验收标准
覆盖 AC-12、AC-13、AC-14。

## 任务清单
### Task 1 — 后端启动 smoke 脚本 [AC-12, AC-13, AC-14]
- 文件：`backend/scripts/smoke_memory_sdk_backend.py`
- 修改：import main → 初始化 _session_db → append 消息 → 断言 facts/recall/twin。
