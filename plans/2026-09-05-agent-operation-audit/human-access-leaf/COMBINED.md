# 审计查看入口：主组合验证

最后更新：2026-09-06。独立源码1097b272、组合c53caff2均经只读固定复核限定ACCEPT；未发现P0/P1。H0.7.3/M0.6.12/S0.3.12专用venv，未改变SDK制品或依赖。

## 组合行为与合并

记忆面板默认提供“操作记录”入口，用户明确打开后以真实已绑定HUMAN连接获取服务端用途绑定授权，只输出最小Memory操作元数据；分页动作持久保存，同一逻辑页重试复用已保存结果；未知状态不重读、不重发业务。关闭、过期、身份变化后拒绝旧授权，审计授权不进入普通Agent Context。

合并时PrimaryMemoryPanel保留既有viewportShown/claimInitialReveal、图谱owner key和遗忘ACK生命周期，只追加审计tab；其余产品修改直接合入。原图谱显示和遗忘确认修复没有被旧分支覆盖，组合独审已逐项核对。

## 实际结果

| 检查 | 结果 | 资源 |
|---|---|---|
| human_access、全部primary_control_binding、semantic_correction及model_recall_selection | 54PASS/31.70s | 峰值236992KiB，墙钟32.44s |
| 新增实际WebSocket审计分支及原普通读分支 | 2PASS/1.29s，4项未选择 | 峰值183904KiB，墙钟2.06s |
| 审计、实际父组件、ControlChannel、认知操作与图谱九个前端模块 | 44PASS/1SKIP，3.58s | 峰值418720KiB |
| TypeScript noEmit | exit0 | 峰值606400KiB |

后端只用本树PYTHONPATH，SDK来自既有专用安装环境；禁止导入本地模型，2GiB/180s进程组看护。前端单worker/无文件并行，1GiB/120s看护。所有测试进程已退出，没有模型、native、浏览器或新构建。

1个前端SKIP是原有HOST_GRAPH_FIXTURE未设置的真实SDK/API关系数据夹具用例；不算通过，不代表真实关系图验收。普通React/DOM测试不是原生点击测试。54和2的数量有重叠，不相加为独立测试总量。

新增WebSocket证据经过实际main控制路由、真实签名绑定、生产factory及安装Memory公共接口：隔离库seed一笔真实suppression，audit open→page获取非空元数据→用不同transport request重试同page_action，持久reads/delivery均仍为1→close→后续page拒绝。测试只替换外围服务启动，不启lifespan/实际监听端口/模型；不称原生E2E。响应不带私有测试source原文或服务端receipt/cursor。

## 命令与证据

后端通过本地bounded_pytest.py，用 `-q --tb=short -p no:cacheprovider` 执行：

- `backend/tests/operation_audit/test_human_access.py backend/tests/memory/test_primary_control_binding.py backend/tests/memory/test_semantic_correction.py backend/tests/sdk_adapters/test_model_recall_selection.py`
- 新增测试后只复验 `backend/tests/memory/test_primary_control_binding.py -k default_control_socket`，产品未改变。

前端在tauri-app：本地vitest执行 `auditRequests.test.ts PrimaryAuditPanel.test.tsx PrimaryAuditBinding.test.tsx PrimaryMemoryPanel.test.tsx PrimaryChatView.test.tsx ControlChannel.test.ts cognitiveRequests.test.ts PrimaryMemoryGraph.test.tsx MemoryGraphCanvas.test.ts`，选项 `--maxWorkers=1 --minWorkers=1 --no-file-parallelism`；然后 `tsc --noEmit -p tsconfig.app.json --incremental false`。

原始证据仅本机ignored `.local-test-evidence/2026-09-06/audit-access-combined/`：

| 文件 | SHA-256 |
|---|---|
| backend/identity-bounded.log | d780153ab6b80ecfac85dd113ebe7381c089f8166e4ed541a7104691f7fe6697 |
| backend/identity-bounded-resource.json | 2f07bf0caf48ede99b7ee04436987e0e910d4c0045c09f5a392fd139d256dfad |
| socket/identity-bounded.log | 143bab9060b8a7f8622d939bf18179bda8d36db8408779be584eaf75085728ec |
| socket/identity-bounded-resource.json | 642da85ec0e03941934a70c952732690ffc69434ee413d3e8eef7477c2cc7db2 |
| frontend/combined.log | 80048b822749b73e3f63f318bd23ceb3f295b78ed0aa6bf8733fe39a52f9f86c |
| frontend/combined-resources.json | 643705a2da28efbadfdd452c2a9dd11b12cdbd0dbc6541051145e6dc6a9ab41f |
| frontend/typecheck.log | e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 |
| frontend/typecheck-resources.json | a1debfd325d471394b24747596e88ca28f6cedfcf809a9ce0b97bbeb8ae4f0fa |

## 保留边界

此入口仅Memory操作元数据，不能声称已提供Harness/Service全部操作的统一审阅或审计推理。SDK只有sealed_evidence_audit用途，Host约束其投影，未伪称SDK实现更窄用途。初始SDK snapshot扫描成本不由UI分页大小约束；实际send开始之后的所有并发窗口、原生UI、完整程序验收仍未覆盖。

独立叶子40/25历史测试及反例日志保持原样；原失败不被新组合重标。未切换主工作目录/原生安装，未push/tag/发布。
