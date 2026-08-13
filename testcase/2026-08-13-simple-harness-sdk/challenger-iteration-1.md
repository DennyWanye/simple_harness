# Testcase challenger iteration 1

## 缺失的测试场景

- [SDK-AC-4/5] 超长 ToolResult/LLM payload 只在自动化 negative 中出现，缺真实桌面 UI 的有界显示与可恢复出口。
- [SDK-AC-5/7] durable HITL 只测批准，缺拒绝和用户 cancel 的真实 UI 终态。
- [SDK-AC-6] fixed exact inputs 已覆盖不同意图，但未列同一意图的自然口语、中英混合、无实现关键词表达，无法审计是否贴 fixture 过拟合。
- [SDK-AC-8] full-surface 文档只列计划可推导入口，没有明确要求汇合闸导入产品公开 surface inventory 后逐项归零。

## 步骤/预期不清的用例

- `prepare-black-box-env.sh`：先 `mkdir` 后验证 evidence 路径，路径错误时仍可能在允许目录外创建空目录；应先做字面前缀检查，再创建并做 realpath 二次检查。
- SDK-S3 Run B：应明确它是随机性/长上下文采样，不增加 distinct input class。
- SDK-S5 重跑：应明确重复安装计数与 package identity 不变，而不只是“不重复安装”的口头结论。

## 建议新增的 testcase（含步骤与预期）

- TC-UI-B1：让受控 Tool 返回接近公开长度上限的合法结果；桌面应保持可操作、上下文/日志/SQLite 有界，并提供明确截断或 artifact 出口。
- TC-UI-B2：durable HITL 分别点击拒绝与取消；应形成可解释 terminal/cancel，不执行未授权 Tool，不留下重复 child/delivery。
- Natural-language probes：对 durable/personal/capability 各补自然口语或中英混合改写，明确只算 retry/路由鲁棒性，不冒充 distinct scenario。

## 输入广度盘点（输入敏感功能必填）

- distinct 输入类别：7 个（纯回答、只读 Tool、durable 多步骤、Personal 候选、Capability build、malformed payload、crash reconciliation）。
- 重跑/改写/continuation 冒充：S3/S4/S5 的第二 root 是随机性采样，不是新的 distinct 类别；当前文档需明确。
- required 仍 PENDING：全部。Phase 3D 只定义 oracle，未执行是正确状态。

## 结论

- AC 主干覆盖完整，但 UI 极端载荷、拒绝/cancel 与表达广度仍不足以冻结为最终 black-box oracle。

VERDICT: FAIL

