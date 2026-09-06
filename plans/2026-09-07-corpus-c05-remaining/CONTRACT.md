# C05 remaining：状态来源组与逐格缺口

2026-09-07；base `c5b5538782dec1ca34ff2e203cd73c777f077dd3`；分支 `feat/corpus-c05-remaining`。源码阶段，**NOT_RUN**。H0710/M619/S0313 不变；不建环境、不启动资源。主完整 candidate/真实 target 统一执行。

## 基准与逐格事实

原定义：Memory 仓库 `plans/2026-08-29-human-memory-digital-twin/quality/recall-corpus-candidate/review-zh/successor-12x20/05-task.md` 的 setup、first input、独立 scripted_followup。设置只消费 `corpus_c05.py` 中固定 setup/hash，不从 followup/gold 推定来源。当前正式 04/09/14/20 + empty 的主六绿（含相邻 parser）直接保留，不重写或重测。

| 格 | 当前已存在 | 正式链仍缺 / 分类 |
|---|---|---|
| 01 | 真实同名 CREATE、resume 来源 | 日期/介质不在现设置 producer 的实际参数中，普通披露没有对应来源；来源接线/披露缺口，不能把整段 setup S1 存在当字段可见。 |
| 02 | 旧录音/音频真实 scope、resume | 2024/2026 的可披露事实与生产近期目录排除 A 未接；日期来源及近期目录控制。不能只删评分历史代替目录事实。 |
| 03 | 现名 CREATE、resume | 原标题变更历史、可检索别名尚未生产写入。现 mutation kinds 无 title rename/alias；不将 alias 拼入标题冒充改名历史。 |
| 05 | 两标题已明确含 2024/2025，实际 title S1 + resume | 原 `date_disclosure` 标记不能一概推断缺 SDK 日期字段：年份已在真实公开标题。需实际同源回读及正式 scheduler 接线，无需伪造独立日期 receipt。 |
| 06 | 两不同 title/Scope | project 仅编译 DTO 值；search 的 checkpoint metadata.project 不等于普通披露。需真实项目生产来源/reader，不得直接写 metadata 绕证明。 |
| 07 | 有限 source 控已真实创建 C/A 并支持 queue scope | **本叶**：actualmain 首轮 C admission/start/initial receipt/物理 snapshot；预览后新无绑定 Run 接 f1。不能将所有预览必须 unbound 的旧调度套给 C。 |
| 08 | 有限 source 控已真实 complete A、只读 open | **本叶**：actualmain setup→complete 搜索→新 Run 公开 resume→物理回读；每轮检查旧 canonical revision/status/terminal 不变，不为完成档案发文件或 task mutation 许可。 |
| 10 | 实际 public page/cursor 的有限 source 控 | 正式评分分页事件未接。真实排序 `score ASC,source_sequence DESC,task_scope_id ASC`；source_sequence scope-local，不能用跨 scope 创建先后或碰巧 UUID 证明 B/A 必然顺序。需公开排名/分页的可构造来源条件。 |
| 11 | 实际顺序读控，有一次固定身份 B/A 结果 | 同 10 的稳定排名来源缺口；不挑 ID、改 result 顺序、重复创建直到命中。正式 scheduler 未接。 |
| 12 | 双 subject helper 与本人 permission-first reader | actualmain 单 subject composition 到真实第二 owner lane 未接；不得只换字符串或另空库当他人任务。 |
| 13 | 合格 title/resume | 独立家庭地址 S1 与真实 suppression→混合字段回读尚未构造；不填造具体私人地址、不把整 scope 隐藏当保留候选。 |
| 15 | title | 预置 2026年8月公开来源、真实缺 provenance 备注与字段级隐藏缺；不将缺备注=未登记备注替代拒读控制。 |
| 16 | 同一中性合成标题、纸质/数码仅 spec | 同月值须明确合成定义；同源首轮最小披露→预先许可补充字段/第二搜索事件未接。不能临时翻权限或从 f2 回填主题。 |
| 17 | 真实 binding reader 验 exact child root helper | 设置 CREATE 仍默认 root；缺首个 binding 就是展板-甲/乙的真实注册及父目录未授予反控。不能后追加第二 root 或改私库。 |
| 18 | 真实 revision mutation helper + history 非连续 setup IDs 能力 | 尚未接候选后独立 fixture Provider Run→实际 revision→退出 fixture→f1 scoring，当前源 hash 与保留旧 scoring history 的跨相位复核。不得在旧绑定 Run 换 Provider。 |
| 19 | 两 scope + binding reader，无初始目标可表达 | 同 17，first named workspace 一/二尚未生产创建；不能两个 scope 共用默认 root 后声称隔离。 |

`unicode61` 当前完整中文 title token 与短词不是同一检索语义。此前 10/11 完整标题查询绿只证明该查询的实际分页，**不证明短中文或自然语义搜索通过**。本叶正控用公开 status 查询检验状态/调度，不评价模型检索质量。

## 本轮完整来源组：07/08

复用唯一 main、TaskSetupHttpProvider、C05SetupApproval、原 S1/route/marker/closure、真实 terminal、TaskScopeSearchStore 和披露 reader；不新增生产 ledger 或 SDK API。

1. 原 setup phase 创建真实两个 scope，公开 source/terminal 校验后冻结 setup history prefix。`state_contract` 只是 runner 观测数据，包含真实来源/旧 terminal/canonical revision；不成为授权，不发送给评分 Provider。
2. `execute_scoring_turn(initial_scope_ref=None)` 增可选 existing scope；07 仅首轮传实际 C，其余行为默认不变。原 `enqueue_turn` 执行 owned admission/原 workspace binding，Host 生成真实 initial receipt。07 评分首个 physical request 必须含真实 C 普通披露 snapshot，不用 USER 中“可信Host”字样当证明。
3. 07 preview 前提为：SDK start 的 C/id/hash与 `ContextRouteLedgerStore.read_route_receipt` 一致，最新 task decision 仍原 host_initial。此时 `no_formal_scope_authority=false`，另记 `initial_scope_retained=true`；不伪称无绑定。未出现可验证非空候选仍停止，不发 f1。原其它格仍要求无正式 scope。
4. f1 经真实新 Run、queue scope=None 入场；只原公开候选/current disclosure 可以授权 `resume_existing`。不跨 Run 搬旧 initial authority，不放宽同 Run 单 Scope。
5. 08 的 complete 是真实 task.complete 状态；公开 resume 的只读结果照常交下一 physical request。每个 scoring terminal 后，按该 Run 原 disclosure 重新 open/render 所有原 scope；要求 status、canonical revision、fields 未变、旧 SDK terminal event/hash 原样。读取可能推进 projection source，故不把新 source hash 一律当业务修改。
6. 缺 current disclosure、变更 canonical/state/来源不可读都停止并保留实际观察；不伪造 terminal、可见字段或任务完成。所有 fixture/scoring Run 分开统计。

## 最小新增控制源码（仅主执行）

`backend/tests/quality/test_corpus_c05_state_phase.py`，复用现正式 child 实际 main/vendor target；原五项 selectors 不变、不请求重跑。

- `test_actual_main_state_source_scoring_phase[C05-07]`：真实 C 初始物理 snapshot；公开 search 返回候选后按实际不同 scope 取引用；新 f1 Run 无初始绑定、真实 resume→下一 HTTP；旧 C/A revision/status/terminal 不变。
- `test_actual_main_state_source_scoring_phase[C05-08]`：真实 complete status 搜索、完整披露结果→exact resume→下一 HTTP；旧 complete/revision/terminal 不变，无重新写文件/更新 scope 权限。
- `test_actual_main_active_preview_cannot_switch_before_confirmation`：真实公开 search 后在首轮请求其它 scope；原审批端保留真实 pending 并 `CorpusApprovalBlocked`，不生成用户 deny/allow；0 后续 scoring HTTP/0 f1，Host latest task association 仍原 host_initial C。

受控 HTTP 按实际公开候选身份行动，只检验执行链，**不是 gold 语义选择/自然模型质量**。编译可生成 oracle 文件供父计分，但运行区禁止读取；post-execution review 保持原独立边界。新源码未运行，原失败不覆写；模型服务阻塞独立存在。

## 主首红与输入入口修复（NOT_RUN）

主 `0f3` 首跑07在 main 初始化之前被 `corpus_first_batch_scalar_input_required` 拒绝，1FAIL0.72s，PG87968已空；08与提前选择负控未运行。原日志保留于主 `.local-test-evidence/2026-09-07/corpus-c05-state/r1/tmp/test_actual_main_state_source_0/c05-phase-child.log`。这是正式入口没有消费 compiler 显式 unresolved 结构，非实际 scope/current disclosure 路径的产品红。

已只读核对原编译产物：07 `current_user_message=null`、`recent_messages=[]`；完整原文 `可信Host当前任务：课程备课，当前scope取实际绑定快照。用户：暂时看旧的藏书编目任务，先搜给我确认。` 在 `unresolved_source_text`。compiler 特意不猜分角色；08无此混合字段。

本修只为07增加精确原文 contract，非通用冒号/正则角色拆分：USER部分为原固定子句，单独 S1+queue；Host部分不进USER/SYSTEM，而是要求真实 setup C active/title来源，生成 runner 观察 `authored-input-binding.json`，随后原owned admission→SDK initial receipt→首个 physical snapshot 二次兑现。输入原件不改，source_text_hash用 `corpus_trace.digest(original_string)`（JSON canonical string UTF-8 SHA256），不声称原始文件 hash。其它 unresolved、一切非空C05 recent仍明确拒绝，绝不放行后丢弃。

现3个节点只修07 child的独立期望USER边界并核物理快照/原Host混合句不出站；不增新测试组。交主仅复原红与两项未跑；本树未起测试。18独立WIP已暂存，未混入该入口修复。
