# 模型短期召回：安装候选验证

最后更新：2026-09-06。隔离 feat/model-short-recall，base a0764047；独立代码审查待固定提交。原程序未完成，用户主 checkout/runtime 未切换。

## 行为

模型显式 memory_types 与 include_short_horizon 进入同一次公共 typed RecallPlan；空长期选择仅在 short=true 合法。长短期共用预算。实际选中的短期 item 使用 Memory0613 新公共来源接口，经当前 Host primary/epoch/S1 完整因果来源复验；fragment 保留 SDK result/item 和 public payload hash。缺来源不曝光，取消传播，下一次实际 Provider preflight 再检查，晚遗忘会阻止物理发送。默认工具能力启用，无灰度 flag。

成功调用记录两项模型选择；失败、取消与 timeout 的完整提案观测尚缺，不称全操作审计完成。工具多消息生产、全主体 projection 成本、真实模型质量和原生 UI 仍待后续。

## 实际安装身份

专用 venv 从组合测试支持环境克隆，include-system-site-packages=false，三 SDK 从本树 vendor 用 --offline --no-deps --reinstall 安装，非空环境重新求解。逐字节核对 installed 与 wheel 所有成员（仅排除 RECORD）：Harness073 169、Memory0613 75、Service0313 121。PYTHONPATH 仅 backend（Host），没有 SDK 源码覆盖。

- H073 wheel SHA256：1a9ed5c95e6cddd4e0ccd85124320a6001008740a53213712fc89f3467cb4cd7。
- M0613 source f2a6a706c5e3407e896ada3bd9e735cd9c0b77fd；wheel SHA256：33fcc494f0cb8c9f358e411b372d4dcdc423725563dda6f5b96684f5fa1ffd62。SDK owner 独立空环境/两次构建复现有单独 ACCEPT，本记录不替代它。
- S0313 wheel SHA256：26205f89854e27bd7ed8cbd6f7ac1f6b621603f973a081823bc6b707ae0784a8。
- uv lock --offline --project backend：仅 Memory0612→0613；H/S不变。保留历史 wheel。

## 分批检查与失败历史

运行器统一为 `.local-test-evidence/2026-09-06/model-short/venv/bin/python <batch>/bounded_pytest.py -q <tests>`，环境 PYTHONPATH=backend。单个 pytest 进程组2GiB/180秒上限，禁止 torch/transformers/句向量等模型导入；未命中上限。所有运行 PID 已退出。

1. `backend/tests/memory/test_model_short_recall.py backend/tests/sdk_adapters/test_model_recall_selection.py backend/tests/execution/test_model_short_outbound.py backend/tests/memory/test_selected_short_runtime.py backend/tests/execution/test_primary_dependency_v2.py backend/tests/sdk_adapters/test_sdk_candidate.py`：53 PASS，31.31秒，PID24610峰值266832KiB。包含真实11组完成对话/索引、only-short/混合请求单调用、参数拒绝、reopen、来源缺失与取消、实际工具账本→MockTransport最终发送/拒绝。混合请求这批只有短期命中。
2. 补充非空长短期混合结果 + `backend/tests/memory/test_cognitive_typed_barrier.py`：2 PASS/1 FAIL，PID24862。失败是新 fixture 错读 user envelope 不存在的 evidence_refs[0]，尚未走到召回。两个旧认知来源出站检查通过。
3. 修正 user envelope ID 后只重跑新混合 case：1 FAIL，PID24895。fixture 未 bind_primary，read_evidence_pair 拒绝；未改变生产拒绝语义。
4. 使用真实 authority.bind_primary 后仅重跑该 case：1 PASS，3.91秒，PID24963峰值137264KiB。通过公共 mutation API 从真实 S1 来源建立 semantic fixture（非模型分析），同一 query 实际返回非空长期及短期：同一 result、不同 item、short仅绑定自身完整来源，一次公共执行且无旁路查询。生产代码在这三次 fixture 修复中未变。

没有将这几批累加成一次全量运行或计划完成率；没有真实 Provider、native、401矩阵或240模型评估证据。`git diff --check`通过。

## 本机原始证据索引

仓库忽略目录 `.local-test-evidence/2026-09-06/model-short/`；原始日志/身份/资源记录未提交。

| 相对文件 | SHA256 |
|---|---|
| installed-identity.json | fb5ec234cf72ea58673f95780a2e3938e05dc20a29c43c613cb43fe3700770d1 |
| identity-bounded.log | 5f319254b29f3105d588b6c5fad153e34d2a8767d0478d730eed8053d73204ef |
| identity-bounded-resource.json | 5d9e7b327c7cc2ebeb4a6574cb8b8f55190e5c90b9e2bcac52ac7b1c7deacf2c |
| mixed/identity-bounded.log | 22bbc9acfbfd98564335877febeb51c25af6188a49e6bb28c500a8f2259a3d59 |
| mixed/identity-bounded-resource.json | f1095e23eaf89a7a4e9f125d3a472f1dc4be01a3975038f5edf7e8b5c1bd9650 |
| mixed-fixed/identity-bounded.log | aca6d3a26c7f4d58e2d1d0b5ccbaa3b4cba65b39ca768a324f60ebcc6d4e3413 |
| mixed-fixed/identity-bounded-resource.json | e5d60fcbd0f6696a196f4757fbb55d6f83ad4206123055aca10bdc4e966993c9 |
| mixed-bound/identity-bounded.log | b13095cb26bc991dba9d454da924596d63192a96be22044fa73bc4b333478dba |
| mixed-bound/identity-bounded-resource.json | 32365da09f995a71491900259038269f066b9e3d0ab8bd7702d0731de892a9d4 |
