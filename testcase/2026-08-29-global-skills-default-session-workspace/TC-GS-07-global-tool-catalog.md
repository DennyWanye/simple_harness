---
id: TC-GS-07
purpose: Verify every Session discovers one complete descriptor catalog while execution remains policy and availability gated
status: active
surface: desktop-ui
type: hybrid
obligations: [TO-A5, TO-A8, TO-R3]
tags: [tool-catalog, descriptor, authorization, availability]
entrypoint: tool search and describe
revision: 1
---

# TC-GS-07 — 全 Session Tool catalog 与执行门禁

## 步骤与预期

确切 Tool ID、availability 类别、独立 product inventory oracle 与 effect canary 冻结在 `verification/policy-fixture.json`；三类 Session projection 必须与该独立 authority 对账，不能只彼此比较。

| 步骤 | 真人操作 | 预期结果 |
|---:|---|---|
| 1 | 在旧、automatic、selected 三类 Session 搜索/描述 policy fixture 指定的 `read_file`、`plan-test`、`gs:test:*` 与 `mcp:filesystem`。 | 三者 descriptor identity/schema/source 集合与独立 product inventory snapshot digest 完全一致；不可执行项仍可发现并显示固定原因。 |
| 2 | 比较 Provider callable subset。 | 只包含当前 eligible ToolSpec；差异必须由明确 eligibility 解释，不是 Session/Project 裁剪。 |
| 3 | 真调用 unavailable Tool。 | 返回真实 health/platform/workspace unavailable 原因，零执行副作用。 |
| 4 | 真调用 confirm-only 与 deny 示例。 | confirm-only 仍要求用户决定；deny 稳定拒绝且零副作用；可发现不等于获权。 |

## 决定性证据

- 三类 descriptor/callable 摘要、UI availability reason、授权决策与 effect count。
