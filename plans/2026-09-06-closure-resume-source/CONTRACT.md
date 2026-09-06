# 非空 resume 的实际来源闭环

2026-09-06，`feat/closure-resume-source` / Host base `ff2f2009`。native持槽期间只编辑，尚未测试。不碰main ACK/prepare、SDK、schema、旧hash或原库。

实际入口：主模型公开 `task_scope_update` 支持 `resume.update` / `goal.revise`，`TaskScopeUpdateService.apply_closure` 产生真实mutation decision与closure receipt，`CanonicalTaskScopeStore._reduce` 将原operation.value写入state.resume/goal。当前普通 `render_scope_disclosure` 仅证明CREATE_NEW title/goal；closure guard因此拒绝非空resume。该路径可达，不能仅删拒绝条件。

最小接线：新 `task_scope/mutation_disclosure.py`，复用既有canonical revision/plan/closure receipt、Host primary effect index及SDK公开effect读取，证明最后一次写该字段的实际operation与模型调用/成功结果精确绑定；按原S1 refs与该生产Run完整依赖作当前policy检查。整段文本精确相等才供closure发送，不截断后冒充完整字段。普通scope projection可保留原有展示预算与完整text hash；旧projection按原selected字段验证，不能因新来源能力而静默扩张旧manifest。

后台fallback不是SDK tool effect：对本轮新成功mutation，复用既有S1 store在原decision/attempt成功同TX追加真实public Provider response的Host来源carrier，绑定原attempt/ordinal/input S1/hash/plan；不创建第二ledger，不改原attempt/result/idempotency hash，不把Hostcarrier冒SDKreceipt。旧fallback只有result_hash而无完整response，继续明确来源不可证明，不回填。不能使用原result_envelope_json列无条件新增response而改变既有evidence-set重放选择。

field reader只披露最新已证明operation；若latest被遗忘/篡改/缺proof，不回退旧同名值。refs仅scope相同不足，须精确S1+subject以及current Memory gate。派生字段作为原依赖union进入scope manifest，旧USER/history/独立short仍受原门控制；慢读取后的当前head/token最终比较保留，access审计不删。未知/missing legacy来源继续省略普通字段、closure pending。

必要新反例（未运行）：实际tool resume.update→下一Run读取非空resume→closure真实出站；fallback mutation→原子response carrier→同样闭合；来源遗忘/plan或effect混接拒读；latest不可见不回退；旧无carrier fallback不可冒证；receipt/事务故障无半提交；旧冻结projection不因新增字段变化。只测新改变，不重跑closure9、analysis14、cold等旧绿。

Compaction定位：`main._build_product_agent_loop` 只有定义，无当前调用；`build_agent`只由该旧工厂调用，context_compressor仅从service_context读取、未发现生产实例注册。当前primary为SDK ReAct生产链，未发现同类后台模型compaction出站入口。本叶不复活旧compactor，不称已完成原全部compaction目标；如后续实际入口出现另核其真实snapshot/reservation契约。
