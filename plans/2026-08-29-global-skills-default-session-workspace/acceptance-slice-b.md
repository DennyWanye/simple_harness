# Release Slice B 验收：用户全局 Skill 生命周期

本文件是 `acceptance.md` 的发布切片视图，不修改或降级原始条款。共享范围、非功能边界、Assurance contract 与完成定义仍以原文件为准。

| ID | 验收条件 | 优先级 |
|---|---|---|
| AC-3 | 正式入口安装只产生一个用户全局 managed publish 与可审计 receipt；无 Project/Session 副本；精确版本重放幂等。 | 必须 |
| AC-4 | 旧 Session、默认目录新 Session、选择目录新 Session 在下一 fresh Run/refresh 均可发现并调用 Skill，冷重启保持。 | 必须 |
| AC-6 | fetch/校验/publish/bind 任一步失败不暴露半版本、不破坏上一 catalog，且可安全恢复。 | 必须 |
| AC-7 | Settings、Chat、fresh Run、continuation 与冷重启消费同一全局 authority；legacy loader/dormant runtime 不作为成功路径。 | 必须 |
| AC-8 | UI 的安装中、成功、失败、已安装状态来自可验证 global receipt 和 runtime visibility proof。 | 必须 |

Required scenarios: `S-GS-03`, `S-GS-04`, Skill portions of `S-GS-05`, `S-GS-06`, `S-GS-08`.
