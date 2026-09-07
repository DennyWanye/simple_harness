# Human Memory Digital Twin 剩余交付

更新：2026-09-07。本页按交付结果汇总，原 plan 的验收要求仍保留。新增专项测试的数量不能直接折算为原矩阵完成率。

| 剩余交付 | 已有事实 | 尚未闭合 |
|---|---|---|
| 提醒完整闭环 | 时间触发、来源校验、失效来源 SDK 终局协议及 Host schema52 已有专项验证 | Host 终局消费与跨库恢复6项已限定通过并合并；50/51/52启动入口兼容9项新增控制分批通过并独审接受，原生52重启仍待验；A7展示/ACK五路由、来源继承与终态恢复已独审合入，当前H078/M618直接路由ACK通过；事件触发真实发布来源及相应端到端验收由用户明确延期至F01；r14时间提醒原生FAIL：后台pending已创建、前台否认且到期下一轮无提醒，Host调度登记0，生产生命周期修复四项通过并合入，r16原数据已触发，但普通回答被SDK pending/no_recall拦截，UI无回答/提醒，展示链继续修复：原ACK/mandatory-exit语义不变，SDK新增有界同Run拒绝反馈/继续接缝，H079新制品与3项安装组合通过；r17旧提醒送达/下一轮去重、r18冷去重通过，但r18新提醒真实ACK后正文未展示，完整送达仍FAIL；独立notice已通过7backend/2UI并独审合入；固定ff35fb82正确18120构建r22新松柏提醒独立正文、r23冷启动保留/后续无重复均真实通过（NATIVE-R20-R23.md），原r18历史FAIL保留，完整A7其他路由仍另验；原A8仅要求循环意图首次一次性到期，递归调度属于backlog |
| 授权过期和重启恢复 | H077 源码 21 个唯一新增用例分批通过，真实故障库副本恢复通过；制品与 installed 公共消费已独立限定审查通过。Host `ebb81b6f` 已完成公共接线和冷恢复修复，3 个唯一场景分批通过 | 原生旧任务已FAILED且可新对话；后置Provider缓存清理修复已审查合入666b475b，r9启动未再观察到该KeyError；完整授权场景仍按各自证据验收 |
| 图谱及召回原生验收 | r6 实际 Provider 生成长期偏好；UI 遗忘成功；r7 重启后仍不展示该记忆 | r8 长期命中、遗忘后零命中、图谱两节点/筛选单节点均实测；旧画布空白本次未复现原因未定。短期投影祖先缺失及后台未生成索引两处修复已合入fa7580b0；r9内部有候选但typed选择仅FTS，预算裁成空。FTS+VECTOR已独审合入0bedaa87；r10暖态、r12新进程首查均真实通过；原r11首encode超时已由启动编码预热修复。r15公开fixture有向关系画布/边详情/筛选恢复已真实通过；模型抽取关系及性能/质量基准仍待做 |
| Procedure 与后台处理 | 原 S3 文档注明 SDK Procedure 消费能力已完成；Host 提案与召回已有相关类型入口 | v4创建与M618旧协议恢复已合；真实模型暴露混正文schema问题，v5分支schema修复后3源码控和3真实分类分别通过，旧v4持久响应在v5恢复零新调用；Scope独立观察正向已在三真实Scope验证并限定独审接受（DRAFT/ELIGIBLE/ACTIVE、成功数1/2/3），恢复边界13项及发现新6项已独审合入（含撤回旧误绿后实际遗忘重验）；M619共同制品与安装组合通过，原生r24仅记录可见，后续停止后补出context_route及重复tool_search，未完成Procedure使用/文件核验（NATIVE-R24.md）；r25原生真实procedure_discover返回0，初次task/Procedure状态混淆保留FAIL；已从r24公开实际响应确认只有episode，非编译器降级；v5.1分类提示修复6个限定控制通过，真实正反例2次Provider分类及公开mutation写入通过（DRAFT+Episode / Episode-only），durable分析job与原生正向使用另验；任务结束物理接线及真实resume锁修复九个场景分批通过并合入；非空resume来源五项已审通过并合入；历史大工具内容分页4个唯一控制已独审合入；当前运行分页2个唯一控制已独审合入，新H078/M618两项组合通过，32k及8k小参数场景的1MiB实际结果分页分批通过；4k保护组超预算时真实收尾/冷重开负控通过，不计4k分页成功；生产fallback的原未闭合写Scope撤回/冷恢复两项已通过并独审合入，保留真实FAILED与pending债务；只修多余来源正文构造和重放状态，未将任务视为完成，r19独立旅程第3轮提前complete/漏核验、第4轮恢复成功但编辑被生命周期拒绝，原生停止后第5轮普通回答正常，完整旅程仍未完成 |
| 非 SELF 输入与完整操作审计 | Host 已接入 V2 来源和终局观察，新增及受影响 8 项测试通过 | 非SELF单个当前输入的完整principal、用途/审计和最终物理请求检查已源验并独审接受，M619后继wheel/主current-input与Draft混合组合已通过，240双项输入及原生披露仍待验；r12真实审计98条已enumerated但driver coverage未验证，H078正式驱动选择已接入并完成安装/Host检查；r13新普通对话45/45审计和driver coverage已实际通过；完整 Host/Harness/Memory/Service 操作覆盖仍待验收 |
| 原计划整体验收 | 240 条语料 AI 审查与主审已完成 | C01/C02/C03/C04共80条setup公共SDK准备已分批验证（C04为19+1跨批，新游标5控已审合0f6b2aba，原H078/M618叶证据不外推当前组合），含修订/遗忘和独立关系图fixture；C01 CREATE真实SDK setup job、graph backoff拒假成功、普通source/scoring隔离三控分批通过并独审合入，C03-20两来源合法收尾/拒绝不误报/取消恢复3项已审叶通过，C02/C03完整prepare和跨进程proof两控已独审合入，当前H079的C02完整prepare组合1PASS已补，C03完整prepare组合及typed/short跨库隔离仍待做；实际质量已尝试C01-10一条，真实1次调用但未召回A，原gold FAIL；评分worker保存业务终态后退场挂起，被180s外部deadline清理，非RSS/磁盘门；新增C01-13首次尝试亦因参数拒绝未召回A，15物理调用、自然退场修复已生效；C01-20在nonstrict修复后亦因无关参数拒绝、无A/B，合计3个不同case尝试/0通过，其余237条未执行；SDK0.7.10 nullable新候选installed实际main组合1PASS；C01-20显式r4复验仅1次请求HTTP400，无模型响应/工具，仍EXECUTION_FAILED，已补有界错误诊断以确定原因；旧FAIL保留（[首例](../2026-09-07-corpus-c01-scoring/REAL-R1.md)）；剩余矩阵；两轮真实模型流程，含长对话、TaskScope 与重启 |

## 计数边界

- 401 项矩阵历史去重结果：224 项有 PASS 记录，177 项尚无 PASS。不是当前完整候选的完成率，也不是 177 个已确认缺陷；本次未重新运行或重算原矩阵。
- 240 条语料的审阅完成不等于质量评测通过。当前尚无该整批真实质量评测完成结果。
- 新增审计 8 项及 Host 公开升级 3 个唯一场景已通过，不能直接从原矩阵未完成数扣除。
- H077 安装验证及故障副本恢复，不代表已恢复原生用户数据。原生已按正常产品路径运行r8/r9；历史失败证据保留，没有手工修补原数据库。
- Host 候选当前固定依赖为 H0710/M619/S0313；H0710一次制品、生产身份与实际main安装组合通过，详见../2026-09-07-corpus-c01-scoring/INSTALLED-0710619.md；原H079/M618及H078组合证据保留。r13普通对话与自动审计是之前H078/M617组合证据，完整新原生旅程未完成。用户主 checkout 未切换。

## 当前推进顺序

当前主隔离候选 H0710/M619/S0313。独立真实POST已确认服务拒绝 model_not_found / unknown provider for model gpt-5.5；模型目录仍列该模型不能证明路由可用。模型取舍问题已提出，未获选择前不偷偷换模型或反复POST，不处理TokenSeller生产。原240仍3个不同case尝试、0质量通过，其余237未执行。

继续不依赖真实服务的准备与接线：C01-06原同ID修订经actualmain真实typed route进入下一受控HTTP请求已通过；C07实际main recent/setup与独立评分Run隔离已通过；C05新增07/08/10/11及TOOL身份共5唯一控制分批通过，正式04/09/14/20多轮phase仍在组合；C08已有标量组件及四类retained摘要源码，新增actualmain首测进行中，不重跑13旧绿。C05新authority实际empty与缺证据区别已通过，但open decision读取pending effect返回None，审批接缝尚待修，不称整链成功。

Host diagnostics未等待coroutine已修，两个新异步控制+三个受影响同步控制5PASS；当前新main组合尚在验证，Memory SDK自身诊断版本字段硬编码另待。以上专项控制不能折算原401矩阵或240质量通过率。后续仍有其余语料准备/质量、Procedure durable与原生正向使用、Manual及两组原生长旅程、完整审计和性能。F01仍是唯一用户明确延期项。

资源密集工作仅主完整candidate/current installed target共享锁串行，子代理目前只写源码/审查。新失败共因优先停批修复，只复原红与尚未执行项。全阶段caffeinate保持，整个测试结束后才解除。

## 详细证据入口

- [原生 r12](NATIVE-R12.md)
- [原生 r11](NATIVE-R11.md)
- [原生 r10](NATIVE-R10.md)
- [原生 r6/r7](NATIVE-075616.md)
- [V2/终局观察测试](../2026-09-06-prospective-source-audit/SUCCESSOR-617.md)
- [Host 公开升级测试](../2026-09-06-prospective-source-audit/HOST-617-UPGRADE.md)
- [Host schema52](../2026-09-05-human-memory-s5c-preparation/SCHEMA-52.md)

H077 固定源码 `c29af66902dd0b418ab12c1c8f871182280f77bd`，wheel SHA-256 `60f7fb164f65ca98a1aa54e4fc75cd7abbc08e409a6a10fe397106d47148c5a3`。其原始证据在 Harness SDK 工作树 `.local-test-evidence/2026-09-06/decision-terminal-recovery/`，由实现者和独立审查者报告确认；本页没有重复扫描旧证据。

## 用户明确延期

发布成功事件的实际来源接入与对应端到端验收移至 [F01](FOLLOWUPS.md)，本次不继续推进、不计PASS；其他原计划交付仍继续。

## 2026-09-07 taiwan Mac 追加（main 组合 H0.7.10/M0.6.20/S0.3.13）

- 时间提醒原生旅程 r4/r5 在 main 上真实通过（登记→到期调度→下一轮呈现→auto 模式零提示 ACK→独立提醒卡片→冷重启保留、无重复），见 `../2026-09-07-native-main-journey/NATIVE-R4-R5-REMINDER.md`。默认生产 schema 实测 54（本页上文"schema52"表述已过期）。F01 事件触发仍延期；递归提醒仍 backlog。
- 记忆旅程 r1/r3 通过（写入/召回/遗忘/重启/遗忘后零召回），见 `../2026-09-07-native-main-journey/NATIVE-R1-R3.md`。
- 用户 09-07 产品决定：auto 模式不弹任何授权提示（已实现）；遗忘只针对记忆不隐藏会话记录（Memory 0.6.21 候选实现中）；F02/F03 记为 followup。
