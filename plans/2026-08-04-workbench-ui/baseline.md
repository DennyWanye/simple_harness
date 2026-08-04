# 绿色基线（执行前快照）

- 日期：2026-08-04
- 基线 commit：644ab16（docs: phase-A acceptance + phase-0 recalibration）
- 平台：macOS (aarch64)

| 检查项 | 命令 | 结果 |
|--------|------|------|
| 前端测试 | `npx vitest run` | ✅ Test Files 107 passed (107) / Tests 933 passed (933) |
| 类型+构建 | `npm run build`（tsc -b && vite build） | ✅ 通过（chunk 体积 warning 为既有状态，非红） |
| Rust 单测 | `cargo test --lib` | ✅ 72 passed / 0 failed |
| Rust 编译 | `cargo check` | ✅ 零 error（10 个既有 dead-code warning：mac 构建下 Windows-gated 调用点所致，见 control_command_canonical，属既有状态非本次引入） |

## 既有非绿项（如实声明）

- cargo check 的 10 条 `never used` warning：control_command_canonical.rs 的函数在 mac
  编译面下无调用方（调用点在 cfg(windows) 内）。fork 起即存在，不阻断、不计回归。
- vite build 的 chunk >500kB warning：MessagePanelRoot 大 chunk，既有状态。
  工作台改版删除 message-panel 入口后此 warning 预期变化，不作为回归信号。

## 回归比对规则

phase-3/4 收口时上述四命令重跑：任何新增 FAIL/error 即回归，阻断交付；
测试数量因删除桌宠测试而减少是预期内变化（WB-9/12），以"剩余测试全绿"为准。

## 补记（2026-08-05，波次 1 后）

- **backend companion 套件本机既有红（改动前即存在，组 R 逐条 md5 比对确认非本次引入）**：
  9 failed / 641 passed / 10 skipped——test_performance durability-lane ×8、
  candidate_draft_receipts（v19→v20）、skill_pack_adapter 库存 hash。T16 收口按
  "不低于基线"判定：companion 回归以 9F/641P 为基线（007 净增 1 绿）。
- pytest 需 `PYTHONPATH=<repo根>` 且 venv 已增量装 pytest 9.1.1 + pytest-asyncio 1.4.0。
- **lint 存量债务（phase-4 门序首查如实登记）**：`npm run lint` 存量 166 errors / 42 个
  fork 前既有文件（controlWs 35、SettingsProviders 18、App 16…），deskpet 时代未做过
  lint 清理。本次改版新增/改造文件 lint 零错误（5 处新文件错误已修，3 处迁移模式定点
  豁免带理由）。lint 门口径 = 零新增错误。
