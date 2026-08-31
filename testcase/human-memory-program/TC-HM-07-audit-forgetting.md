---
id: TC-HM-07
purpose: Verify raw evidence is permanent while append-only suppression removes content from every ordinary path
status: active
surface: desktop-ui
type: hybrid
obligations: [HM-TO-A1, HM-TO-A7, HM-TO-R1, HM-TO-R3, HM-TO-R5]
tags: [human-memory, append-only, suppression, audit, rebuild]
entrypoint: primary conversation, ordinary read, and controlled audit
preconditions:
  - Exact Harness and Memory candidate wheel paths, SHA-256 pins, source commits, and versions are available
  - Python 3.11 or newer and uv are available for the isolated clean-wheel consumer
  - Fresh ignored artifact run directory does not already exist
revision: 2
---

# TC-HM-07 rev2 — 永久原始证据、逻辑遗忘与受控审计

## Sealed public-consumer authority

- fixture：`fixtures/sealed-audit-v1.json` revision 1，SHA-256
  `57d6758f4c42dd18bcf1bd843cd000de31cafda7a04ec820236505db5843a758`。
- runner：`runners/run_sealed_audit_public_consumer.py`，SHA-256
  `d290b529dba99799c9f5d59b9ae5f101ac33054398cf40f4bb097d9dcc7b3c62`。
- Harness：`simple-harness-sdk==0.7.0`，commit
  `fb491574db8bb4d19d8a7f9df0c72ae460bb08f4`，wheel SHA-256
  `36522c4abce5ba598e084a9c45aca0fb32ded2b9e8d9bc3eb8c28694eb39b99f`。
- Memory：`simple-harness-memory-sdk==0.6.0`，commit
  `d069e0e949edb44d85d2ebeac6e49b05f9fb51b1`，wheel SHA-256
  `6f9a8c1a52cf8512232a12a9ea7fd7d98c6c5d59e837d2c5f777010bb30fe6b2`；第二次构建字节一致。
- clean consumer 只从 `simple_harness` 与 `simple_harness_memory` package root 导入公开 DTO、builder 和
  `MemoryManager` facade；禁止私有 submodule、source checkout、repository object、SQL 和产品测试 helper。

自检只证明 fixture known answers，不证明产品：

```bash
/Users/denny/projects/simple-harness-memory-sdk-memory-plan/.venv/bin/python \
  testcase/human-memory-program/runners/run_sealed_audit_public_consumer.py --self-check
```

正式命令：

```bash
/Users/denny/projects/simple-harness-memory-sdk-memory-plan/.venv/bin/python \
  testcase/human-memory-program/runners/run_sealed_audit_public_consumer.py \
  --harness-wheel /tmp/simple-harness-task5-wheel3.MtoX75/simple_harness_sdk-0.7.0-py3-none-any.whl \
  --harness-wheel-sha256 36522c4abce5ba598e084a9c45aca0fb32ded2b9e8d9bc3eb8c28694eb39b99f \
  --harness-source-commit fb491574db8bb4d19d8a7f9df0c72ae460bb08f4 \
  --memory-wheel /tmp/simple-harness-memory-task7-closure-a.N0ny0w/simple_harness_memory_sdk-0.6.0-py3-none-any.whl \
  --memory-wheel-sha256 6f9a8c1a52cf8512232a12a9ea7fd7d98c6c5d59e837d2c5f777010bb30fe6b2 \
  --memory-source-commit d069e0e949edb44d85d2ebeac6e49b05f9fb51b1 \
  --artifact-dir /absolute/path/to/.local-test-evidence/sealed-audit-task7
```

PASS 必须证明：公开 builder 新建 fresh v6 DB；sanitized evidence 保留 raw source hash 绑定但不保存原始凭据 canary；
外部 audit authority reference 经 `authorize_audit_access` 生成 sealed receipt；同一 receipt 分别读取 sealed evidence、
sealed trace 和 canonical manifest；关闭并从同一 DB 公开 reopen 后，逐表 row count 不减少，count 不变的 root 不漂移，
count 增长的 root 必须变化；manifest 的 64-hex schema/init/principal/table/payload/access hashes、逐表 count 和总 count
独立校验。原始 DB/result 仅留 ignored artifact 目录。

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

## 质量与 UI 边界

该 clean-wheel deterministic PASS 不替代真实主模型 trace 质量、逻辑遗忘全表面、桌面 UI 或两轮真实模型评估；
这些 required gates 仍为 `NOT_RUN/BLOCKED`，不得由本 runner 推导 program quality PASS。
