---
id: TC-HM-07
purpose: Verify raw evidence is permanent while append-only suppression removes content from every ordinary path
status: active
surface: desktop-ui
type: hybrid
obligations: [HM-TO-A1, HM-TO-A7, HM-TO-R1, HM-TO-R3, HM-TO-R5]
tags: [human-memory, append-only, suppression, audit, rebuild]
entrypoint: primary conversation, ordinary read, and controlled audit
revision: 1
---

# TC-HM-07 — 永久原始证据、逻辑遗忘与受控审计

## 步骤与预期

| 步骤 | 真人操作/探针 | 预期结果 |
|---:|---|---|
| 1 | 记录住址并完成一次普通召回；冻结 raw row IDs/content hashes。 | 原始证据、提取 decision、active memory 和使用 lineage 均可按 ID 关联。 |
| 2 | 输入“忘掉我之前说过的住址”。 | 追加 suppression/decision；不物理删除、不覆盖任何旧 raw evidence 或 lineage。 |
| 3 | 依次通过普通 memory recall/search、TaskScope search/open、六阅读视图、ResumePackage、短时域、Context、图谱、exact ID 和旧 checkpoint 尝试读取。 | 所有普通路径立即不可见；普通 trace 也不泄露内容。 |
| 4 | 故意让一次派生更新失败，再重建 FTS/vector/read views/projection。 | suppressed 内容仍不复活；重建前后 raw row count/content hash 不减不变。 |
| 5 | 明确请求“为了审计，告诉我当时为什么记住它”。 | 生成有 purpose、subject、范围和时效的 AuditAccessDecision；只在受控审计面最小披露，访问本身留痕且权限不能被普通 Agent 复用。 |
| 6 | 明确撤销遗忘。 | 追加新的恢复决策；旧 suppression 和 lineage 不被覆盖，只有新状态允许的内容重新可用。 |

## 物理删除禁令

- retention、维护、测试清理、容量压力和 schema forward-fix 各路径都要比较 raw row count 与逐项 SHA-256；任一减少即 FAIL。
