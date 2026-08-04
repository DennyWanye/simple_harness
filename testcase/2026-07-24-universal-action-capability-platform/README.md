# 单主 Session 通用行动与能力包验收

当前状态：**PARTIAL / BLOCKED**。VS-1、VS-2 已完成真实主消息页验收；B-2、B-3、B-6
只覆盖了部分边界。其余 required 场景仍待执行，B-4B 还必须由用户本人处理 Windows
Secure Desktop UAC。

- [完整真人用例](./manual-test.md)
- [验收条款](../../plans/2026-07-23-universal-action-and-capability-packs/acceptance.md)
- [AC 证据账本](../../plans/2026-07-23-universal-action-and-capability-packs/evidence-ledger.md)
- [自动化与真人证据目录](../../plans/2026-07-23-universal-action-and-capability-packs/manual-results-2026-07-24/)
- [幂等性审查](../../plans/2026-07-23-universal-action-and-capability-packs/checklists/idempotency-review.md)

自动化当前已通过聚焦 `206`、backend `5961`、Frontend `820`、Rust `73`、Godot pack
与 capability/construction/authority 门；strict last-mile 复跑为 7/7 PASS、0 skip，
`DECISION: SHIP`。

真人矩阵包含 VS-1/2、B-1～B-7、S-1～S-6。任一 required 场景仍为
PENDING/PARTIAL/NOT RUN 时，本文档和项目状态都不得标为完成。

已完成的真实证据：

- VS-1：root `4eeb23a0d3f05fb38a32e1d8e7727054`，模型查询真实能力目录并在同 root
  激活/执行 `file_write`、`run_shell`。
- VS-2：root `74e21a97b26852b3a22047b7fb2eb37a`，Auto 下创建 27-byte 文件，
  PowerShell 首次校验失败后由同一父模型重规划，最终 PowerShell 与 Git Bash 双重验证。
- 隔离环境精确清理：最终 launcher 树 13/13 退出，survivor=0，18120/15193 归零，
  释放 8533.9 MiB private memory，用户主实例 8100 保留。
