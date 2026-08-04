# ARCHITECTURE 事实源与 STATUS 兼容入口手测

> 被测功能：`ARCHITECTURE/` 是项目架构与状态唯一事实源；`STATUS/` 只保留历史链接兼容。

## TC-01 — 新接手入口

1. 打开 `AGENTS.md` 与 `CLAUDE.md`。
2. 确认顶部均指向 `ARCHITECTURE/index.md`。
3. 确认 HARD 纪律标题为 `ARCHITECTURE 更新纪律`。
4. 确认通过测试后要求同时更新模块架构与 `ARCHITECTURE/PROJECT_STATUS.md`。
5. 确认规则明确禁止继续向 `STATUS/` 双写正文。

通过条件：两个项目级指令文件口径一致，且不再把 `STATUS/status.md` 设为事实源。

## TC-02 — 架构索引覆盖

1. 打开 `ARCHITECTURE/index.md`。
2. 依次点击 `PROJECT_STATUS.md`、`ARCHITECTURE.md`、`AGENT_HARNESS.md`、`AgentLoop.md`、`DeepResearch.md`、`SEARCH_GATEWAY_DEEPRESEARCH.md` 与 `PPT.md`。
3. 确认文件均存在且可打开。

通过条件：索引覆盖全局状态和现有主要模块架构，没有 404。

## TC-03 — 全局状态内容完整

打开 `ARCHITECTURE/PROJECT_STATUS.md`，确认包含：

- 活跃 worktree；
- 核心功能模块完成度；
- 最近里程碑；
- 已知问题与测试纪律；
- 文档索引；
- ARCHITECTURE 更新纪律。

通过条件：原 `STATUS/status.md` 的有效信息已迁入架构目录，顶部最后更新日期保留。

## TC-04 — 旧路径兼容

依次打开：

- `STATUS/index.md`
- `STATUS/status.md`
- `STATUS/AgentLoop.md`
- `STATUS/DeepResearch.md`
- `STATUS/PPT.md`

点击其中的新路径链接。

通过条件：旧文件只包含迁移说明和可用跳转，不再包含第二份状态正文。

## TC-05 — README 与 plans 导航

1. 打开 `README.md`，确认描述 `ARCHITECTURE/` 为唯一事实源，并说明 `STATUS/` 只做兼容。
2. 打开 `plans/index.md`，确认落地状态指向 `ARCHITECTURE/index.md` 与 `PROJECT_STATUS.md`。
3. 打开根 `acceptance.md`，确认 DoD 要求更新 ARCHITECTURE，STATUS 只验证兼容跳转。

通过条件：所有活跃入口采用同一维护口径。

## 自动检查

```powershell
rg -n "STATUS 更新纪律|以 \[`STATUS/status.md`\].*为准|必须同步更新 \[`STATUS/status.md`\]" AGENTS.md CLAUDE.md README.md plans/index.md acceptance.md ARCHITECTURE
```

期望：无匹配。
