# ARP 下一版逐项决定模板

此文件是交给 PlanAgent 的空模板，不是已经完成的裁定。用户范围固定为原生 Runtime；不加入 Pi/其他引擎适配。

| ID | 状态 | 唯一决定 | 主计划/Schema/SQL/API 改动定位 | 真实来源或 NEW 接口 | 关闭证据/尚缺依赖 |
|---|---|---|---|---|---|
| Q01 引用类型 | PENDING_PLAN_REVISION | | | | |
| Q02 检索分页 | PENDING_PLAN_REVISION | | | | |
| Q03 设置往返 | PENDING_PLAN_REVISION | | | | |
| Q04 基线与接缝 | PENDING_PLAN_REVISION | | | | |
| Q05 模型计量 | PENDING_PLAN_REVISION | | | | |
| Q06 真实 embedding | PENDING_PLAN_REVISION | | | | |
| Q07 授权与 bootstrap | PENDING_PLAN_REVISION | | | | |
| Q08 原事务接入 | PENDING_PLAN_REVISION | | | | |
| Q09 冻结请求失效 | PENDING_PLAN_REVISION | | | | |
| Q10 长 turn 与 N | PENDING_PLAN_REVISION | | | | |
| Q11 索引版本与 DDL | PENDING_PLAN_REVISION | | | | |
| Q12 检索覆盖与资源 | PENDING_PLAN_REVISION | | | | |
| Q13 后台 job | PENDING_PLAN_REVISION | | | | |
| Q14 生命周期与锁 | PENDING_PLAN_REVISION | | | | |
| Q15 唯一 registry | PENDING_PLAN_REVISION | | | | |
| Q16 Skill 闭环 | PENDING_PLAN_REVISION | | | | |
| Q17 DTO 大小与披露 | PENDING_PLAN_REVISION | | | | |
| Q18 错误事件类型 | PENDING_PLAN_REVISION | | | | |
| Q19 迁移与保留 | PENDING_PLAN_REVISION | | | | |
| Q20 验收与准备门 | PENDING_PLAN_REVISION | | | | |

最终状态使用 `RESOLVED / MAPPED / ENVIRONMENT_DEPENDENCY / OPEN_CONFLICT`，同时说明是规格关闭、静态映射还是实际行为证据，不把三者混为一谈。

仍有冲突时，每项给出：Q编号、精确来源、互相不兼容的两项语义、推荐选项、影响的接口/字段/验收。不要只写“需用户确认”或“实施时处理”。新目标尚未实现不构成语义冲突。
