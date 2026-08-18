# Plan：记忆 SDK 接入 slice 3 — main.py 接线 + 后端 smoke

## 主要矛盾
把 slice 2 的 SessionDB+MemoryBackend 接线落到真实 app 启动链路：`main.py` 构造并注册
`SessionDB(memory_backend=...)`，失败降级 None 不阻断启动，再跑后端级 smoke 验证该模式。

## 关联验收标准
覆盖 AC-9、AC-10、AC-11。

## 任务清单
### Task 1 — main.py 真实接线 [AC-9]
- 文件：`backend/main.py`
- 修改：模块级构造 `SessionDB(memory_backend=SQLiteMemoryBackend(...))`，注册 service_context，失败降级 None。

### Task 2 — 编译 + 后端 smoke [AC-10, AC-11]
- 文件：`backend/tests/test_memory_sdk_integration.py`
- 修改：`test_main_wiring_construction_smoke` 覆盖构造模式往返。
