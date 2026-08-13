# Testcase challenger iteration 2

## 缺失的测试场景

- 无。SDK-AC-1～8 均有分步 testcase；正常、错误、空/缺 Port、边界、非法输入、并发、幂等、崩溃恢复与发布/cutover 均有明确出口。

## 步骤/预期不清的用例

- 无。S5 的抽象 exact input 已用同一 Product Session 的公开目标上下文消除歧义；仍不依赖实现关键词。

## 建议新增的 testcase（含步骤与预期）

- 无 required 新增项。执行阶段若产品公开 surface inventory 比当前计划入口更多，必须在 gate init 前扩充 full-surface 清单；这属于 fail-closed inventory 对账，不改变冻结业务 oracle。

## 输入广度盘点（输入敏感功能必填）

- distinct 输入类别：7 个：纯回答、单只读 Tool、durable 多步骤、Personal 候选、Capability build、malformed/unknown payload、crash reconciliation。
- 重跑/改写/continuation 冒充：S3/S4/S5 第二 root 与自然表达 probes 均已明确只算随机性/路由鲁棒性，不增加 distinct 计数。
- 跨领域：项目检查、个人规划、能力构建；低证据/错误态：S6；恢复态：S7 与 supervisor restart。
- required 仍 PENDING：全部场景尚未执行；Phase 3D 的正确状态是 NOT_RUN，不被错误标作 PASS。
- 正向价值：S1～S5 共 5 类，均要求真实 Provider、非空业务结果和独立 quality bar。

## LLM 行为对抗

- 乱序/重复：S6 按 run+call+effect correlation 断言。
- schema 违约：S6 覆盖缺失、额外、错类型、枚举、stale/forged。
- 长载荷：S6 自动化有界断言 + TC-UI-B1 桌面可用性出口。
- 拒不调用 Tool：S6 由 hard termination gate 收敛。
- 随机性：S3/S4/S5 最少 2 个完整 root，S3 含一个 ≥10 轮历史 run。
- 冷启动：SR-9 明确 `stateful_init=false`；产品首次登录进入 follow-up，但 clean wheel/schema init/reopen 未放宽。

## 结论

- 当前黑盒套件覆盖完整、步骤可执行、场景计数诚实，并能发现旧 authority、路由旁路、重复副作用、secret 泄漏、exact-wheel 漂移与 LLM payload 违约。

VERDICT: PASS

