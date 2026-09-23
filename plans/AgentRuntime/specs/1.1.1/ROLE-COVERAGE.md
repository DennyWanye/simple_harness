# 原生角色接线覆盖

所有下表路径是hand-off已报告/既有职责；实际集成parent逐条记录call edge/hash。

|角色|入口|必须满足|
|---|---|---|
|root standalone chat|AgentRuntime.create / BaseAgent.submit|explicit STANDALONE owner policy; B/C/D NOT_APPLICABLE is construction mode|
|child/delegate|AgentRuntime child/delegation factory|child config/owner/session independent; original creation ticket reused|
|create_many|AgentRuntime.create_many and existing batch ledger|all staged until final batch visible, no inference during create|
|HTN Planner / Manager / MethodSynthesizer|agent_orchestrator/runtime/agent_worker.py::AgentBridge|original typed service dispatch → BaseAgent → same prepare hook|
|Worker|AgentBridge|original Task/Attempt/manifest owner; no second task|
|Critic / Verifier / root / Mission judge|AgentBridge + actual Assurance review coordinators|each actual invocation through original ProviderInvocationCoordinator; preserve official exposure|
|backend LLM-native planning|existing HTN dispatch|same BaseAgent adapter not direct client; non-LLM solver tools unchanged|
|recovery|AgentRuntime.open/startup original kernel restore|frozen calls replay exact; no recomposition of old identity|

不允许任何Planner/Critic/Judge绕过计量/冻结直接调用Provider SDK。若发现旧角色直调，通过原ProviderInvocationCoordinator+owner binding接适配，不创建第二执行器。非LLM solver工具不假装需要聊天Context。
