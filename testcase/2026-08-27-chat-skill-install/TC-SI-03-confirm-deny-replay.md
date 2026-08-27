---
id: TC-SI-03
purpose: Verify the human owns one exact batch confirmation and deny, expiry, mutation, duplicate, and cross-channel replay fail closed
status: active
surface: desktop-ui
type: hybrid
obligations: [TO-SI-3, TO-SI-R1, TO-SI-R3]
tags: [skill-install, confirmation, deny, replay]
entrypoint: chat and Settings URL install
revision: 1
---

# TC-SI-03 — 确认拒绝与重放

## 步骤与预期

| 步骤 | 真人操作/脚本夹具 | 预期结果 |
|---:|---|---|
| 1 | 聊天 stage 有效 batch，真点击拒绝。 | intent durable settle 为 denied；无正式目录、binding、Manager success receipt 或 catalog 变化。 |
| 2 | 重放同一拒绝和旧 approve continuation。 | 返回同一 settled 结果/结构化 already-settled；绝不发布。 |
| 3 | 新 stage 后等待 token 过期再 approve。 | 返回 confirmation expired 与重试出口；staging 被精确回收，catalog 不变。 |
| 4 | 新 stage 后分别篡改 digest、Project identity、candidate list 或 principal，再 approve。 | 每次 fail closed；原 intent 未被错误消费，不产生部分发布。 |
| 5 | 在 Settings stage 后从聊天重放响应，并对同一合法 receipt 重复提交。 | 跨 channel 仍由同一 service 校验；错误上下文拒绝，合法重复只结算一次。 |
| 6 | 注入乱序/重复 tool result、缺 URL/scope/Project 字段、超长 URL/摘要、模型只给 shell 命令或直接声称成功。 | 状态按 intent/revision 归约；schema/size 有稳定错误；无伪确认卡；success-claim gate 阻断旁路文本。 |

## 通过条件

- 所有拒绝/重放路径零正式安装副作用；真实用户只需对完整 batch 决定一次。
- UI capture 与 durable intent/receipt/目录计数共同取证，历史 PASS 不代替本轮执行。
