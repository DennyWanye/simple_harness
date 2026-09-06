# 主组合只读核对及两控准备

2026-09-07。主固定 `0e146792b97eb54c63d33dec4c0337f5fee985df`，检查时 clean。本次 **NOT_RUN**；Singer 持槽，没有启动 pytest/模型/native/安装，没有修改主树或原数据库。

合并检查：`main.py` 的本叶 delta 仅 context_route/task_scope_search 说明；生产 ContextRouteToolService 使用真实 binding authority、ScopeDisclosureReader 和带 evidence_ingress 的 ContextRouteLedgerStore。`context_authority.py` 保留已有主线代码，仅追加 task_only、require_unbound_run 及 specific rejection；在原 BEGIN IMMEDIATE/idempotent replay 之后、route INSERT 之前重核关联。测试 build 保留主线 visibility_checker/procedure_runtime 的调用位置，同时保留本叶 context_route_ledger_factory/write_file_schema 透传。只读未见覆盖错误，尚未作新组合动态验证。

## 安装载体与计划两控

复用 `/Users/denny/projects/simple_harness-primary-candidate/.local-test-evidence/2026-09-07/memory619-artifact/installed`。三 SDK dist-info 声明 H079/M619/S0313，direct_url 均为主 backend/vendor；本次只读 metadata，没有重复全成员核验。Memory manifest source `e27003c68b892fe061aac0ca2c9a140871f564cd`，wheel SHA `c95cdf4852c3ca07a6d62f40aa3c8559f2715e7f4dfa966f9d412f8c063509d1`。旧叶7绿仍是 H079/M618，不挪用为 M619 结果。

新 carrier 位于本独立树 `.local-test-evidence/2026-09-07/completed-scope-619-prepared/run.py`，**尚未执行**。只选 `new_active_scope_physically or already_bound_run`，加载主 Host 源及上述 target；启动前核精确主 HEAD/clean，前后核 HEAD、运行后记实际 SDK 模块来源。主 HEAD 若变化应先重新明确测试源，不静默换源。

主交槽后才执行，在本独立树令 OUT 为新的 ignored 目录：

```sh
MAIN=/Users/denny/projects/simple_harness-primary-candidate
PY="$MAIN/.local-test-evidence/2026-09-06/primary-m0615/venv/bin/python"
"$PY" "$MAIN/scripts/run_resource_bounded.py" --evidence-dir "$OUT" -- \
  "$PY" -I -B .local-test-evidence/2026-09-07/completed-scope-619-prepared/run.py "$PWD/$OUT/tmp"
```

不重跑 Manual/其余绿，不新建环境，不覆盖默认锁/磁盘门。

## Manual 当前没有可直接点击的完整 native 入口

`context_route.py` 的 create_new 已通过真实 append 返回 challenge，并将 new scope +binding_challenge 写入拒绝详情；`sdk_adapters/tools.py:_result` 转为 failed 时只保留 code/message，因此模型和当前工具 UI 不拿到结构化 challenge。`PrimaryRunPanel.tsx` 只消费 legacy project_directory_request 卡片，其 decision/nonce/version 不是这次 manual binding challenge。不能用旧卡片、模型自报路径或 allow bool 替代。

既有受认证 human_memory_request 已实现 `binding.manual.propose(scope_ref,root)` 和 `binding.manual.decide(challenge_ref,decision)`，authority 仍验证真实 challenge/subject/当前政策/root。新复用场景已经产生带原 filesystem identity pin 的 challenge；应接回这个 challenge，不能为省事再 propose 一个丢 pin 的替代请求。最小实际 UI 后继需要可信 Host pending/结果投影按真实 Run/新 Scope 展示该原 challenge（包括被授权的实际 root），重连可恢复；用户显式决定走原 manual.decide，bound ACK 后才允许真实 resume_existing(newScope)，拒绝/过期不得发写权限。当前公开 challenge 结果含 ref/hash/proposal/scope/nonce/expiry/evidence，未含供人核对的 root 显示字段，因此精确 proposal 展示也需可信投影。不在本只读轮实现接口或假造用户决定。

## r24 文件发现：独立只读结论

`tool_catalog/real_tool_manifest.json` 明确有 read_file 与 file_read，分别绑定真实 OS/Workspace 只读 handler。生产 build_explicit_product_tool_catalog 从此 manifest 建 spec，build_product_tool_registry 校验完整 inventory。故源码未发现只读工具注册遗漏；r24 start inventory 不含这两个名字不等于完整 catalog 不含它们，尚需区分 direct 与 deferred。

真实链：main explicit catalog → ProductCapabilityCatalogSourceAdapter → H079 RuntimeToolCatalog → SdkRuntimeCapabilityBridgeAdapter.search。SDK 只搜索该 Run deferred_ids，按 query tokens 在 capability id/description/search_terms/schema 文本出现次数加分，任一命中即可；exact id 有额外加分，稳定排序后按 cursor 分页。Host 仅将 descriptor-only 不可激活项后移，不按读写语义特殊排序。

已确认产品说明漂移：当前 manifest 的 query 写“Every token must appear”，但实现是上述 OR 计分；还声明 toolset filter，实际 bridge handler 不透传 toolset。tool_describe 描述/参数举例引用 capability_search，真实公开工具名为 tool_search。正常 search 结果无统一 describe 下一步提示；真正 describe 成功的 next_action 则已正确要求复制 capability_id/schema_hash/describe_nonce 激活。不能把这些静态差异直接等同 r24 循环的唯一原因。

Dirac r24 摘要证明四次搜索均成功、从未 describe/activate，且 Procedure 两入口已在 start inventory；没有实际读候选完整排名/该 Run deferred 证明时，不宣称排序导致读工具不可达，也不写硬编码调用流程。原 native FAIL 保留，未重跑模型。后继如要修应先用真实冻结 catalog 重现这些 query 的排名与既有合法 capability 可发现性，再修实际暴露说明或已证明的检索缺陷，不从“模型没选”推定权限或强制流程。
