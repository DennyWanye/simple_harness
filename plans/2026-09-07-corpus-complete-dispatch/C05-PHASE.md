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
