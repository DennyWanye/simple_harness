> 2026-09-23 源码交付更新：真实 SDK 开发父源现已随本私有仓库保存在 `sdk/simple-harness-sdk/`，Host 仍固定 HTN wheel；请先读[跨电脑 HANDOFF](../../HANDOFF-2026-09-23.md)。下文“本次提交仅包含计划”指此前 ARP 文档提交，不再表示当前仓库缺少 SDK 源码。Assurance 未完成的生产接口仍不能视为已到位。

# AgentRuntime 计划与评审交接

当前施工规格：**ARP-EXEC-1.1.1**。最新评审结论：**GO，可以进入主体开发**，不是产品验收完成。范围仅增强既有 BaseAgent / AgentRuntime / ReAct，暂不适配 Pi 或其他 Runtime。

## 在另一台电脑上从这里开始

1. 阅读 [1.1.1 主计划](specs/1.1.1/ARP-EXEC-1.1.1.zh-CN.md)。同目录包含严格 Schema、SQL、接口与字段来源、参考代码和验收场景。
2. 阅读 [1.1.1 最新评审](2026-09-23-arp-1.1.1-review/REVIEW.zh-CN.md)，落实结果页预算、终态召回不可改写两条实施注意事项。
3. 阅读 [自动 F 检索合同](specs/1.1.1/implementation/CONTEXT-RECALL.md)、[接口](specs/1.1.1/implementation/INTERFACES.md) 和 [主体接线完成条件](specs/1.1.1/implementation/BODY-WIRED.md)。
4. 从仓库当前 [架构入口](../../ARCHITECTURE/index.md) 核对真实 SDK/TaskGraph/Assurance 父候选、dirty 改动及资源。包内 SOURCE-INDEX 是历史报告，不是已同步到另一台电脑的源码。

本次提交仅包含 `plans/AgentRuntime/`，没有业务实现、未提交的 SDK/Host 候选代码、模型资源或本机测试原始证据。另一台电脑仍需获取并核对实际开发父源与资源，不能凭本计划目录声称这些依赖已经到位。

## 修订与评审顺序

| 版本/记录 | 用途 |
|---|---|
| [1.0 主计划](specs/1.0/ARP-EXEC-1.0.zh-CN.md) / [初审](2026-09-23-arp-1.0-review/REVIEW.zh-CN.md) | 历史设计与最初缺口；其中 Pi 探讨已被后续范围决定移除 |
| [Q01–Q20 handoff](2026-09-23-planagent-handoff/HANDOFF-PlanAgent-ARP-1.0.md) | 一次性问题清单、源码定位与计量器补充调查；修正初审对已有计量实现的不足判断 |
| [1.1 主计划](specs/1.1/ARP-EXEC-1.1.zh-CN.md) / [R1–R5 评审](2026-09-23-arp-1.1-review/REVIEW-AND-HANDOFF.zh-CN.md) | 大部分问题修复后，发现五项局部补正 |
| [1.1.1 主计划](specs/1.1.1/ARP-EXEC-1.1.1.zh-CN.md) / [最新评审](2026-09-23-arp-1.1.1-review/REVIEW.zh-CN.md) | 当前施工版本；R1–R5 主要规格缺口关闭 |

当前用户要求与有效 AGENTS.md 优先于历史计划中的模型、流程和权限描述。主体开发由主代理承担，主体完成前不做大批量回归；最终验收并未豁免。

## 文件完整性与证据边界

各 `specs/<version>/` 为可审阅的仓库文档版：计划、合同、参考代码及测试源码保留；`reports/` 原始机器测试报告不上传，重复嵌套 ZIP 由相邻版本目录替代。每版 `REPOSITORY-EXPORT.json` 记录原 ZIP SHA-256 与省略文件 hash。

`UPSTREAM-DELIVERY-MANIFEST.json` 保留原交付清单；`DELIVERY-MANIFEST.json` 按本仓库文档版重新生成。请勿把后者称为原 ZIP 的完整复现。评审中的 `.local-test-evidence/` 和 `/Users/denny/...` 仅表示当时证据/源位置，在另一台电脑不会自动存在。

最新版本可用已有 Python 执行轻量资产检查，无需先跑产品测试：

```bash
python3 -B plans/AgentRuntime/specs/1.1.1/tools/verify_delivery.py
python3 -B plans/AgentRuntime/specs/1.1.1/tools/check_plan.py
```

这些检查仅证明文档资产和选定合同一致，不证明真实 SDK、迁移、原生 UI 或模型验收通过。
