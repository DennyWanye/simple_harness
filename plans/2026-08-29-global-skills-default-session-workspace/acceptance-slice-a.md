# Release Slice A 验收：普通 Session 工作区

本文件是 `acceptance.md` 的发布切片视图，不修改或降级原始条款。共享范围、非功能边界、Assurance contract 与完成定义仍以原文件为准。

| ID | 验收条件 | 优先级 |
|---|---|---|
| AC-1 | 新建普通会话且未选择目录时，在 OS Documents 下的 `SimpleHarnessProjects` 原子创建 Session 独占目录；canonical execution root 持久化且重启不漂移。 | 必须 |
| AC-2 | 用户选择有效目录时冻结使用该目录且不创建默认目录；取消走 AC-1；非法目录明确失败且无半绑定 Session。 | 必须 |
| AC-6 | 默认目录创建或 Session 持久化失败不产生可误认的半绑定记录；重试可安全恢复。 | 必须 |
| AC-7 | fresh Run、continuation 与冷重启均消费同一个 durable Session workspace authority。 | 必须 |
| AC-8 | UI 清楚显示默认或已选工作目录，以及创建错误的真实状态。 | 必须 |

Required scenarios: `S-GS-01`, `S-GS-02`, workspace portions of `S-GS-05`, `S-GS-06`, `S-GS-08`.
