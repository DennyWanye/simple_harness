# S6 P2 runtime 可组合测试候选

最后更新：2026-09-05；分支feat/human-memory-s6-primary-preparation，前置29902ea4。

## 交付范围

无scope primary实际SDK启动/终态/outbox、真实有界history、旧scoped兼容fixture；
None→生产context_route.resume_existing→tool discovery/activation→生产write_file→TaskScope closure。
binding head更新后不得物理落盘；原scoped冻结authority不放宽。产品authorization和handler在
effect期间使用经真实route/binding重验的exact root，Run admission仍无scope/root。

统一terminal_identity验证Host receipt/SDKbind/observation或ExecutionEvidence/gate/input refs，
再提raw SDK event ID/hash验证实际terminal；generation允许真实crash/reclaim的旧observation。
legacy同run/state换eventID或hash均拒绝；无删除原始evidence的fixture。

control in-flight请求在实际canonical事务前通过原revocation barrier重验当前lease；
main现broadcast增加有界state invalidation，无身份/原文payload，不依赖legacychat_v2_final。
main新增API三kwargs惰性接线，artifact投影保留四字段白名单。

## 组合依赖与尚未完成

需组合Dirac API fbc026a0及使用terminal_identity helper的后继提交；本树不编辑service/api。
未改main checkout、SDK/pins、UI；未起App/真实Provider、未打包/全量测试。
**完整memory-forget history尚未闭合，禁止主生产切换/合main或宣称全部隐私完成**。
Hegel正做Memory公开批量可见性/反向lineage候选；Host保持真实source与receipt原文账本，
没有替换成空history或用假epoch免责。当前API的Host candidate suppression接线继续保留；
已知Host来源、实际能读取的ID/hash及未能展开的依赖见PRIMARY-INTEGRATION-CONTRACT.md。
fresh普通UI首回路可独立测，不能代替Memory-forget或create/manual binding UI验收。

## 验证

命令（本worktree）：

```sh
PYTHONPATH=backend /Users/denny/projects/simple_harness/backend/.venv/bin/python -m pytest backend/tests/execution backend/tests/memory/test_primary_control_binding.py backend/tests/sdk_adapters/test_tool_authority.py backend/tests/sdk_adapters/test_effect_gate.py backend/tests/sdk_adapters/test_effect_gate_hardening.py backend/tests/sdk_adapters/test_effect_gate_replay.py backend/tests/sdk_adapters/test_context_authority_primitives.py -q -p no:cacheprovider
```

**181 passed in 56.37s，exit 0**。真实SQLite/Harness/checkpoint/route恢复和生产effect/terminal；
Provider与authorization响应为deterministic fixture，不声称真实Provider/全部授权UI。
artifact为纯public Context投影测试：SDK0.7.2 Provider输出禁止artifact block，早期fixture
对此的失败已更正为精确投影测试，没有放宽SDK或声称Provider artifact端到端。

## 本机原始证据（不入Git）

- `.local-test-evidence/2026-09-05/s6-primary/runtime/review-candidate.log` — SHA-256 `8510127b555e743d23b69b1cfe66f2b100f3d1f3227b37d38fc491437846a96c`
- `.local-test-evidence/2026-09-05/s6-primary/runtime/dynamic-first.log` — SHA-256 `bddf894575a545c8bd5b26b35fc3088978444802ebce61c2924f041ca08e688e`
- `.local-test-evidence/2026-09-05/s6-primary/runtime/dynamic-fifth.log` — SHA-256 `b6f6da17c2ca58a0a4f5d66b0519c0366b750f91fcbcbf9d42f916fdf18aead5`
- `.local-test-evidence/2026-09-05/s6-primary/runtime/dynamic-negative-artifact.log` — SHA-256 `1116dfc904795006d40a47e217470f12c5895ecaf7a93a3a1f795bd209530530`
- `.local-test-evidence/2026-09-05/s6-primary/runtime/terminal-normalized2.log` — SHA-256 `d4fad80a3b610f24908cf3f62946761b50c70bb12d2a3ecda1e78e72a4941fd9`

dynamic-first包含旧fixture factory缺startup导致route拒绝；dynamic-fifth为正例1pass。
dynamic-negative-artifact为54pass+1个Provider artifact fixture失败；最终181pass已覆盖修正。
历史186pass含已撤销错误删除fixture，不作为当前交付证据；本次真实legacy fixture取代该错误正例。

接口与消费边界见[PRIMARY-INTEGRATION-CONTRACT.md](PRIMARY-INTEGRATION-CONTRACT.md)。

CREATE_NEW后继已单独修复，见[CREATE-NEW-BINDING.md](CREATE-NEW-BINDING.md)；上文74425388验收范围不追溯扩大。
