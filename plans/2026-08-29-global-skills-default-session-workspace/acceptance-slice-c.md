# Release Slice C 验收：全局目录与自动权限

本文件是 `acceptance.md` 的发布切片视图，不修改或降级原始条款。共享范围、非功能边界、Assurance contract 与完成定义仍以原文件为准。

| ID | 验收条件 | 优先级 |
|---|---|---|
| AC-4 | 所有 Session 的 fresh Run/refresh 使用同一用户全局 Skill snapshot，冷重启保持。 | 必须 |
| AC-5 | 所有 Session 可搜索/描述全部产品 Tool；实际执行仍服从 authorization/confirmation/health/platform gate。 | 必须 |
| AC-7 | catalog、fresh/continuation 与恢复消费同一生产 authority。 | 必须 |
| AC-8 | UI 展示全局 scope、catalog generation 与真实 availability reason。 | 必须 |
| AC-9 | 新普通会话默认并持久化为自动模式；Skill 安装先完整预检并生成 durable auto-approved receipt 后无需人工确认；用户显式覆盖保持；拒绝、健康和平台门禁不被绕过。 | 必须 |
| AC-10 | Skill 安装失败返回稳定错误并使 durable Run 与 UI 在有界时间内收敛；真实 waiting 必须有恢复/取消出口。 | 必须 |

Required scenarios: catalog portions of `S-GS-04`, `S-GS-05`, plus `S-GS-07`, `S-GS-09`, `S-GS-10`.
