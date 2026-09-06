# S5c event最小真实来源接缝

2026-09-06，只读现source51树15e8fd0a；主ec9e8765/schema52/M617尚未合本树。time原绿不重跑。

## 原范围

S5c INTERFACES.md:52规定本scope真实发布成功receipt，不可把RunCOMPLETED/终答文字当发布。
README.md:21/23规定recurring只首次一次性，递归调度与新增发布执行器均不属当前实现。
SOURCES.md A7规定occurrence来自Memory inbox，processed仅durable prospective_ack；signal apply不是presented/ack。

## 当前已存在与缺口

- task_scope/store.py:185 append_host_event只接受host.turn/file/test；TaskEventReceipt:51含event_id/task_scope_id/sequence/source_event_id/payload_hash。
- sdk_adapters/effect_gate.py:599 classify_objective_event从实际settled PROJECT_EFFECT ToolResult产生host.test：明确allowlisted command_head、exit_code、outcome、effect_id/call_id；shell=True不归测试事件。不是模型推断。
- 未找到release_succeeded/deployment成功专用producer或receipt。host.file原子写入不等于发布成功。
- analysis_proposal.py:112–122/395生产Prospective schema/compiler仅time，缺可信event_authority_ref选择。
- H076 public ProspectiveEventTrigger和ProspectiveSignalIntent(EVENT_OCCURRED)可用，但EVENT_OCCURRED仍仅pending→triggered；不能假定time的RESCHEDULED修复也覆盖event。
- 当前ProspectiveSignalStore只接受time_due/time observation，名字/receipt域不能伪装event。schema52为主所有，扩event存储需共同定稿，不自改旧timeDDL。

## 最小实施契约（发主协调，不新增兼容产品接口/外部发布能力）

1. 先选已有host.test实际成功源作为独立可执行event能力：精确绑定principal/TaskScope、ToolResult outcome成功+exit_code0、实际effect/call/event receipt。明确这不是原发布成功AC完成。
2. 主提供/接入注册时的可信event选择：event_authority_ref为固定Host域，condition含受支持event_kind及exact task/effect条件；模型只选择已给真实候选，不能自行grant任意字符串。
3. Host reader从既有ExecutionEvidence及task事件关联核验完整receipt/hash和真实effect来源；只读，不新增“事件发生”ledger。不能只校验可任意调用append_host_event产生的自洽payload。
4. 新event source绑定实际ACKregistration与Hosteventreceipt，signal identity=(owner,registration,triggerrevision,actualeventid/hash)，observed_at使用实际event时间；Run/op取原真实registration lineage。signature receipt body另域host_event_observation，不塞time due_at。
5. 共用唯一scheduler claim/handoff/replay流程，event扫描cursor与occurrence/ack分开；持久首次authority、同ref恢复。扩signal-store的event body/domain与schema接受范围需主协调，不能把event当timer/outbox。
6. 公共apply返回后只从Memory inbox获取occurrence_key；presentation同snapshot事务、ack工具及终态settle沿A7另接，不能替代为“发出了event”。

必要新控制：实际settled test成功→原scope匹配event→publicapply/inbox1；failed/unknown/foreignscope/伪造receipt拒绝；同event重开/丢ACK同ref无第二occurrence。事件改期RESCHEDULED公共协议缺口单独保留，不改冻结H076。

下一步不能合法实装“发布成功”生产链的阻断项是缺真实release producer以及生产event注册选择接线；不写ready=false空wrapper。可实施host.test接缝不会宣称原发布AC绿。无需再次问用户授权，由主协调现有模块所有权及event journal布局。
