# Testcase：SDK 易用性优化 — Slice 1 black-box 验证物

> Program: `plans/2026-08-19-sdk-usability-optimization/`（acceptance.md + plan.md + assurance-contract.json）
> 本目录只含 **Slice 1（harness SDK v0.1.2 宿主切换）** 的 black-box 验证物。
> oracle 定稿于实现之前（验证准备轨），禁止随实现改动而放松判据。

## 文件 → AC/TO 绑定表

| 文件 | 类型 | 绑定 AC | 绑定 TO | 绑定场景 | plan 任务 |
|------|------|---------|---------|----------|-----------|
| `api_compat_check.py` | 自动化脚本（stdlib only） | S1-AC-3 | TO-S1-3 | — | Slice 1 Task 6 |
| `surface-smoke.md` | 全表面冒烟清单（复用既有脚本） | —（change-risk） | TO-R1（FAIL-2） | — | Slice 1 Task 9 步骤③ |
| `cold-1-manual-test.md` | 真机人工测试步骤卡 | S1-AC-6 | TO-S1-6 | COLD-1（manual_required=是） | Slice 1 Task 9 步骤①②④ |
| `README.md` | 本索引 | — | — | — | — |

## 本目录不覆盖、由其他轨负责的 Slice 1 义务（仅登记，便于审计）

| AC / TO | 负责产物（不在本目录） |
|---------|------------------------|
| S1-AC-1 / TO-S1-1 | `sha256sum` 对比 vendor 与 SDK 仓库 `dist/` wheel；既有脚本 `scripts/verify_sdk_wheel.py`（复用，见 surface-smoke.md §0） |
| S1-AC-2 / TO-S1-2 | `backend/tests/sdk_adapters/test_sdk_candidate.py`（plan Slice 1 Task 5 新建，篡改 wheel 负向测试） |
| S1-AC-4 / TO-S1-4 | `cd backend && python -m pytest` 对比 phase-2 锁定基线快照（plan Slice 1 Task 7） |
| S1-AC-5 / TO-S1-5 | 宿主 conformance 套件（plan Slice 1 Task 8，切换前基线 20/20） |
| TO-R2 | `uv sync` 重现 / lock 校验（plan Slice 1 Task 3 验证步） |

## 执行顺序建议

1. `api_compat_check.py`（离线、秒级，最先跑——实现未落盘时即可对两个 wheel 预跑）
2. `surface-smoke.md`（dev.sh 起来之后，逐项最小一枪）
3. `cold-1-manual-test.md`（真机，含干净用户数据目录冷启动；与冒烟可共用同一次启动，但 COLD-1 要求**干净数据目录**，注意顺序：先冷启动卡，再起第二轮做冒烟亦可，或一次干净启动内先完成 COLD-1 再跑冒烟）

## 证据归档

执行证据（脚本输出、截图、日志摘录）放本目录下 `evidence/` 子目录（执行期创建，oracle 不预建）：

```
evidence/
├── api-compat-<timestamp>.txt        # api_compat_check.py stdout
├── surface-smoke-<timestamp>.md      # 逐项结果打勾 + 输出摘录
├── cold-1-screenshot-<timestamp>.png # 主聊天非空回复截图
└── cold-1-sdk-runtime-ready-<timestamp>.log  # dev.sh stderr 摘录
```

## 既有脚本复用声明

本目录**不重造**以下既有资产，仅引用：

- `scripts/dev.sh` — 一键启动（tauri + vite + backend）
- `scripts/verify_sdk_wheel.py` — wheel SHA-256 校验（TO-S1-1 用）
- `scripts/e2e_smoke.py` — /health、/metrics、WS budget_status、WS provider_test_connection、WS chat→chat_response
- `scripts/e2e/e2e_text_chat.py` — 三轮 WS 聊天冒烟（首条 prompt 与 COLD-1 相同）
- `scripts/test_websocket_interactions.py` — WS chat + tool_use 观察（含 chat_v2_final 协议）
- `scripts/smoke_test_app_startup.py` — SDK import + 后端独立启动冒烟（注意：会自己拉后端，与 dev.sh 互斥，见 surface-smoke.md 注意事项）
