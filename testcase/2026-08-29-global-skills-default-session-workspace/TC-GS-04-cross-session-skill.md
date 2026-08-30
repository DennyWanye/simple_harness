---
id: TC-GS-04
purpose: Verify standalone and TaskScope Runs share and invoke one immutable global Skill snapshot
status: active
surface: desktop-ui
type: hybrid
obligations: [TO-A4, TO-A7, TO-R2]
tags: [skill-runtime, global-catalog, fresh-run, provider]
entrypoint: fresh chat run
revision: 2
---

# TC-GS-04 — 多种 Run 的全局 Skill 可见与调用

## 前置

- 在唯一永久主对话中准备安装前 standalone Run、安装后 managed TaskScope Run、安装后 exact-root TaskScope Run。
- 已通过 TC-GS-03 安装 `GS-FX-IMMUTABLE-01`；`fixture.json` 另冻结 `GS-FX-IMMUTABLE-02` 作为 N+1；真实 Provider 已授权。

## 步骤与预期

| 步骤 | 真人操作 | 预期结果 |
|---:|---|---|
| 1 | 三种入口分别启动独立 fresh root Run，搜索 fixture Skill。 | 三者解析到同一 global catalog generation 与 exact managed manifest/content identity。 |
| 2 | 三个 Run 分别输入“调用已安装的 plan-test Skill，只返回其已加载 identity 与阶段名称，不实施代码”。 | `skill_invoke` terminal=`succeeded`，manifest/content identity 与 fixture runtime oracle 一致；每轮 Provider invocation 非零并列出该 Skill 的 plan→execute→test 阶段。 |
| 3 | 保持 generation N 的一个 attempt 运行中，经正式 UI 安装 `GS-FX-IMMUTABLE-02`。 | publish 产生 N+1；运行中 attempt 保持 N，不半程漂移。 |
| 4 | 同一主对话启动下一 fresh Run。 | 无需重启 backend 即冻结 N+1；continuation 保持所属 root 的 snapshot。 |

## 决定性证据

- 三个独立 root_run_id/primary_conversation_id/task_scope_id、Provider terminal、tool_search/skill_invoke identity、N/N+1 lineage。
