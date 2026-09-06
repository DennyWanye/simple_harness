# C05 actualmain phase 接线：限定源码交付

2026-09-07：04/09/14/20 的 shared dispatcher/session + 原固定 followup 已实现源码，**NOT_RUN**。新控制全部交主完整 candidate / H0710 M619 S0313；本分支未运行 pytest、模型、安装或资源载体。C08仍partial，其余C05不开放。下方旧“尚未接线”段落为历史准备状态。

## 实际接线与独立观测

- 原 compiler 的 `scheduler/cases.jsonl` 单独落 `scheduler.json`；初始 USER、setup、scheduler、oracle四路保持隔离。worker不读gold。04/09/14只f1，20按原表f1不切换、f2才明确选择。逐条精确校验表中字段、文本和顺序，不能替换fixture action。
- C05不借空标量seed代表准备。真实main创建scope、必要material marker/closure、SDK与Host终态、原S1/source/disclosure全部核验后，才能freeze真实setup prefix；同store保留原facts。认知后台analysis在此fixture阶段关闭，USER通过原ingestion worker取得真实入场ACK，**不称其pending认知job已APPLIED**。
- setup唯一HTTP fixture通过原resolver/physicalguard调用；每次wire hash派生的fixture response id与公开SDK invocation/response/工具call ID交叉核验。fixture自己的计数不单独成为SDK调用证明。源码独立保存每个setup Run，不计入评分Provider统计。
- main构造只用一次固定ignored `runtime/task-workspace`，两条激活链同root；不能写用户默认工作区。原history reader继续current policy，只剔除已证明完整的setup组，后续评分组仍经真实来源读入。
- freeze后必须fixture HTTP close/join、resolver无active binding，再登记`corpus-real-provider`。每条原评分/后续USER由同store真实新Run执行，校验Provider绑定、独立Run身份、SDK/Host完成组以及公开trace；不是初始turn成功就报整格完成。
- 调度事件来自Carver `read_candidate_events` 的真实公开effect/schema/disclosure/current policy；另查公开start的原scope/route为空与Host当前无task route。实际empty是UNMET，不发followup；缺result/source/disclosure等不可验证是OBSERVATION_FAILED，不按empty绿化。任何一步失败均保存已实际发生的Run与请求观测，不救场/重发。
- scoring审批与setup写权限分离。只允许原memory只读、task候选搜索，最后明确选择后才允许`resume_existing`；不授权新scope、文件或task mutation。实际原preview的visible_sources三元组在批准前按当前policy重读，目标来自真实Provider提议和真实公开candidate，不能由setup label/gold决定。错误但合法的候选照原脚本执行，语义正确性留原gold事后复核。
- 每个scoring Run保留独立SDK trace/hash；review packet只聚合这些Run的真实调用/提议统计，setup排除。失败/缺trace保留未知与lower_bound，不伪造跨Run SDK receipt。

## 当前已知待修、禁止抢跑

主首次authority控制确认：open decision尚未执行时公开effect可为None，不能用已执行effect ledger证明pending raw/internal映射。Carver正在修原approval的公开prepared映射。本接线scoring approval也受此边界影响，需消费同一固定helper再启动actualmain新控；不把该fixture准入红归因SDK业务缺陷，不临时推导ID。已消费a0dc37eb的binding exact_receipt四字段修正，真实marker/closure新控会覆盖该深路径。

## 最小新控制（均NOT_RUN）

`backend/tests/quality/test_corpus_c05_phase.py::test_actual_main_task_setup_scoring_and_authored_followups[C05-04]`

同node参数`[C05-09]`、`[C05-14]`、`[C05-20]`，合计4新控制。各自fresh child使用主完整源与实际installed target，无guard override。setup真实loopback HTTP，scoring仅受控HTTP响应；查询固定公开status词，最终选择只取真实工具返回第一候选，**不声称符合原语义gold**。独立检查setup无泄漏、future followup未提前入物理请求、每Run真实终态、20的f1无正式scope权威、最后exact resume和fixture/评分统计隔离。真实模型质量另计。

`backend/tests/quality/test_corpus_c05_phase.py::test_actual_empty_preview_does_not_send_followup`

原两scope真实准备非空，评分发真实无匹配search，要求真实空结果、仅一scoring Run、零f1/f2及零resume。不是用空库满足no-match。缺effect/result/disclosure的权威负控由Carver既有新控提供，不复制同套。

原C07独立分支两次载体FAIL保持：缺tracked assets和借用installed target来源不匹配。主已在完整candidate解决载体并另报61f474e5实际phase通过；这些原失败不覆写，不用本C05源码追认。

---

# C05 正式 same-store phase 合同与构造接缝

2026-09-07，source-only/NOT_RUN。Hegel负责shared session/dispatcher及main构造透传；Carver负责C05 transport/phase reader/exact setup approval/helper。全部后继验证由主完整candidate/实际vendor target统一执行，不在分支安装或测试。

首消费集合仅04/09/14/20。其它case的日期/别名/来源/主体/根/受控版本/分页义务不从这4例推定满足，C08 partial不变。

## 已落 main 的窄构造透传（尚未被session调用）

- `_activate_human_memory_host_ports(..., history_reader=None, configured_workspace_root=None)`：只在显式提供时向原PrimaryForegroundContextPort传入reader。依赖Carver已审constructor hunk；clock、policy、stack/终态reader均保持。默认原PrimaryHistoryStore，非None不替换已绑定runtime authority。
- 同一trusted configured_workspace_root经`_activate_product_sdk_runtime`/`_build_product_sdk_runtime_stack`传入context_route binding store和ProductRunContextAuthority的binding store，并传给Host WorkspaceBindingRuntimeAuthority。默认None保留原产品配置语义；评分caller只可从本次ignored runtime目录固定根，不接模型文本/gold或另一个权限ledger。
- 原main默认store会使用`~/SimpleHarnessWorkSpace`；隔离userdata本身不隔离marker写入。这是需要此同一root构造透传的原因，不改HOME、不借用用户已有根、不先写再补授权。17/19的多exact-root义务不在首消费集合。

## 约定的 phase 接口

Carver `TaskSetupHttpProvider(model=...).start()/registration()/close()`：真实loopback HTTP，不替换main resolver/ProductProviderAdapter/physical guard。传给prepare_scope_archive的provider就是transport；它代理arm/route/marker，helper enqueue后bind_queued，drive从transport.queued取得实际receipt。每次arm保留当前请求hash/计划动作，全阶段记录所有实际HTTP请求。

`C05PhaseHistoryReader(path, subject, delegate=真实PrimaryHistoryStore)`在main构造时安装；setup read仍走delegate。`freeze(archives,stack,primary_ref)`一次核exact setup prefix/terminal/source后切评分视图，只冻结membership/cutoff，不缓存visibility=true，每页继续原suppression/disclosure。setup USER及derived assistant/tool一起过滤，独立scoring USER保留。

`prepare_scope_archive`的新增disclosure_context_resolver由真实queued turn/route SDKRun经`trusted_disclosure.resolve_current_disclosure`解析；调用前后核真实subject/primary/Run，不构造不存在的fixture-reader权限。慢读之后仍有现物理出站current guard。已有静态context参数仅保留旧控制兼容，不用于主新phase。

专用setup decision reviewer必须匹配真实pending Run/turn/effect/tool/完整args/nonce/version及已固定setup动作/实际configured root。只允许当前原setup需要的create、实际marker和closure，不因schema合法或相同tool名就授权，不扩大原ReadOnlyMemoryApproval。

各setup Run必须真实SDK终态＋Host closure/archive已确认；完整prefix冻结后close/join fixture HTTP，resolver无active provider binding，再登记新real Provider供新unbound scoring Run。旧fixture provider identity留作审计，不改旧Run。任何partial/cancel/error停止评分并走owned finally；HTTP已返回或SDK completed不单独代表Host收尾。

## 正式评分还必须接固定followup

原05-task定义：04/09/14首轮真实非空task_scope_search受控披露＋终态后f1；20还要求f1后助手终态且无正式resume，再发f2。原compiler有独立scheduler/cases.jsonl，当前sharedprepare尚未拆出此文件，单turn session不能称完整C05评分。

后继只拆scheduler给runner，不把整表送Provider、不读gold。事件有效性来自真实public effect/result/披露hash与实际terminal；不要求候选含gold A，错误但合法候选仍按固定脚本继续。缺前提record_unmet_and_stop_no_rescue；不救场给ID、不临时改权限。20在最终明确选择前无resume/新增执行授权。最终多个Run完整trace才能交原gold后审；首轮可执行不等于整个case完成。

当前仅main构造接缝WIP固定供协作；transport/approval/phase/followup消费者尚待合入，不开放C05批处理，也不把缺口改名为永久BLOCKED结束任务。源码持续推进，暂不宣称C05正式phase ready。

2026-09-07后续source：`corpus_c05_session.validate_schedule`已固定4个原script表的精确字段/顺序/文本，拒绝任意改写的followup或非none fixture action，不读取gold，不把整表传setup Provider。它只是runner输入预检，尚未接dispatch/event循环；C05批处理仍未开放。Carver新增read_candidate_events由实际Host effect索引＋SDK公开result＋当前披露读取，消费者将直接复用，不自行按A/B重建候选。
