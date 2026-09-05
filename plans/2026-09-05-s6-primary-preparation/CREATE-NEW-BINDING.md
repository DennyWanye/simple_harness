# CREATE_NEW active primary binding修复

最后更新：2026-09-05；独立后继提交，parent 74425388。本树不改main checkout/SDK/pin/UI。

## 问题与实际行为

原create_new把configured_root的CanonicalWorkspaceRoot DTO字符串repr当路径传给append；
即使改为canonical_path，配置父root仍过宽。AUTO又将active primary Run的None scope与新scope
全等比较，必拒stale；load_current_run_binding也只能返回空scope。Manual传入的interaction
标识没有对应durable evidence，不能产生合法challenge。

现在由service先创建owned TaskScope，再用configured.canonical_path/task-{scope_id}调用
service.append_binding。真实binding.append proposal evidence先落库，然后原runtime authority
创建子目录并进行既有canonical root/Manual或AUTO验证。标题不参与路径，未放宽父root检查。

AUTO在active None scope Run的一次绑定操作内安装_PrimaryBindingTarget，包含实际Host Run ID、
owned目标scope/root、proposal幂等键和真实evidence ID/hash。load_current_run_binding每次重读
真实Run/context/lease和owned scope/proposal，给原snapshot issuer提供该绑定目标的authority视图。
现有store在issue/authorize/append/commit前重复验证；目标不是Run admission scope。
不会造新Run或调用无Run bootstrap；原scoped Run仍拒绝给其他scope绑定。操作结束finally清除视图。

Manual由同一service写入真实proposal，原authority发challenge。route拒绝结果中返回
binding_challenge（原challenge_ref/hash、proposal_ref/hash、scope_ref、nonce、expiry、evidence_ref）
及required_action=binding.manual.decide；未添加旧project_directory/external-wait入口。
Manual测试用真实service.decide_manual_binding(allow)验证该challenge可消费、receipt确实提交，
原始evidence保留。**Manual新UI和SDK失败ToolResult到该UI的投影仍未接通/未验收**；不能将
backend handler返回challenge等同自动弹卡、SDK WAITING或恢复成功。

## 验证

真实active None CREATE_NEW → AUTO binding → route receipt → tool discovery/activation →
生产write_file → semantic closure → terminal/outbox通过。核验AUTO request的run_id/context ID/hash
等于真实foreground Run；scope是owned新目标，入队scope仍None；路径是稳定task子目录。
同delivery重试+SDK DB重开不重发Provider、不重建scope/binding。

新增负例：before append真实lease关闭时零binding/零Host terminal；捏造proposal evidence ID/hash
时零grant/零binding/零task route。原scoped跨scope拒绝测试保持通过，workspace binding原验证未改。

Provider及Tool authorization响应是deterministic fixture；AUTO policy使用真实SQLite CapabilityStore，
workspace runtime authority、Host evidence、SDK/ReAct/checkpoint、route、physical write handler和
terminal皆为真实实现。Manual是backend service/challenge/decision测试，不是UI/真实Provider证明。

命令：

```sh
PYTHONPATH=backend /Users/denny/projects/simple_harness/backend/.venv/bin/python -m pytest backend/tests/execution/test_primary_create_new_runtime.py backend/tests/execution/test_primary_foreground_runtime.py backend/tests/task_scope/test_runtime_binding_authority.py backend/tests/task_scope/test_workspace_bindings.py backend/tests/sdk_adapters/test_context_route_tool.py backend/tests/sdk_adapters/test_context_route_authority.py backend/tests/sdk_adapters/test_s5a_milestone_route_loop.py -q -p no:cacheprovider
```

结果：**66 passed in 25.81s，exit 0**。只新增的CREATE_NEW文件为5条；不与66重复相加。
以74425388两份原生产文件运行同一真实AUTO正例，exit 1（无binding receipt），之后完整恢复本修复源码。

原始证据仅本机ignored：

- `.local-test-evidence/2026-09-05/s6-primary/runtime/create-final.log` — SHA-256 `f6ae4cbb386682bd2be07993a14e24ab13adf4873cecdf9c306c6fd2066a0cd6`
- `.local-test-evidence/2026-09-05/s6-primary/runtime/create-auto-confirmed-red.log` — SHA-256 `5a1cbdca6be06a964e26f045d3b731f76537a46c9b2446ee546545ff17640bb9`

## 剩余边界

完整Memory-forget对原始history/反向lineage的suppression仍待Hegel public候选；本提交不解决该缺口，
禁止主生产切换/合main或声称S6全部隐私完成。普通fresh UI首回路须由主独立验真。

最终候选日志：`.local-test-evidence/2026-09-05/s6-primary/runtime/create-review-candidate.log`，SHA-256 `a224cd0b7010ee3e2e0123148f7a1f1df272e2460009027b0413b08a11ef5e7b`。
