# C01 精确公开审批接线结果

2026-09-07。源码 `222346d3 + ac76e16a`，在已验99d17c11后追加；原初始化/trace三绿未重跑。新增组合仅1个，修后PASS；不是真实模型评分，C01真实评分仍0。

## 实际修改

`execute_scoring_turn(approval_driver=None)`原调用默认不变；评分caller显式传ReadOnlyMemoryApproval。drain后先核driver错误，再读真实primary current state/decision；仅SDK waiting且具有公开exact tool decision才响应。完整request与公开turn/HostRun/SDKRun/generation/nonce/version绑定；列表preview不用于授权。

白名单只有工具名context_route且route=memory_standalone，不看gold、query、类型或期望答案，不改变生产policy/Auto，不给模型追加提示。每批全部先核，遇其他请求整批BLOCKED、原decision仍open，不制造deny/ACK。decisionID响应前记attempt，本进程最多50个不同decision，响应不确定就退出，不循环重发；未知Provider等待也不wake重发。

响应使用原service.respond_primary_decision，原writer fence→public SDK decide→真实授权/审计；允许后通过原runtime.after_control重新观察终态。独立本地approval摘要记request hash/目标/结果，不保存nonce正文。BLOCKED在execution显式标记，原Provider失败/观测计分规则继续生效。

首跑还发现原carrier只build、未开SDK ingress，真实Run start因此拒绝（无Provider调用）。ac76e16a调用原 `main._activate_companion_runtime_adapter_and_open_ingress()`，复用已有实际growth/companion/skill-install依赖；不直接写accepting、不替换factory、不启动background worker。初始化绿未曾enqueue，不能替代这个dispatch控制。

## 单一实际组合控制

`backend/tests/quality/test_corpus_scoring_approval.py`使用实际main factory/真实public seed/原生产权限/实际Memory与SDK：首真实Run提出memory_standalone→exact允许一次→实际handler→第二次本地Provider响应→完整终态/两条公开Provider事实。随后同栈新Run提出create_new；未获批准、保持WAITING/open，再visit仍BLOCKED且无新增Provider调用。整个控制禁止socket网络及oracle/.env读取。

仅内部HTTP delegate `_ProductOpenAICompatibleProvider.invoke`被固定本地响应替换；外层ProductProviderAdapter的pre-invoke guard保留，不mock授权/handler/终态。不代表真实HTTP或真实LLM质量。实际Memory召回触发本地WeMM加载，不能称本次无本地模型运行。

| 批次 | 源码 | 结果 | 资源 |
|---|---|---|---|
| approval-r1 | 222346d3 | FAIL：SDK ingress仍关闭；无Provider调用 | PG66255，exit1，6.535s，峰398304KiB，remaining=[] |
| approval-r2 | ac76e16a | 1PASS16.83s | PG66376，exit0，18.700s，峰1049584KiB，remaining=[]，cleanup=null |

默认共享锁2GiB/180s，无lock覆盖。r2最低磁盘599MiB，退出后df606MiB；已通知主，下一真实case需恢复默认1GiB准入再启动，不能绕门。PG清空、共享槽释放，临时vendor链接恢复，installed/r4/gold未修改。原diagnostics coroutine warning继续保留，另外本地依赖deprecation warnings不扩改。

原始文件位于本树 `.local-test-evidence/2026-09-07/corpus-c01-controls/`，不提交Git：

| 文件 | SHA-256 |
|---|---|
| approval-r1/command.log | db72c7940f5d198f28e9e744e802c45ae9147b4f2054dd5c25395114e59985cf |
| approval-r1/resource.json | 0ee7282fb38e4c570a57700409a8f77aea1612c710b161819a3d163fbaca7ad9 |
| approval-r2/command.log | 307693081477181afc280136ff480b7422ab28ebd6eabcf96f83e428bd1e8b21 |
| approval-r2/resource.json | f6a0a0dba5f9698cb7b77eba2d1c2f86000d6fadd01b76400c168c7b3b64e052 |
| run-approval.py | 33b09566cf786ffd84ba6a5bc93282695b3c02494e7b1ba4e866f35abe21c37f |

## 首次真实C01-10

主先合本补丁，再按[RESULTS.md完整命令](RESULTS.md)执行同一个C01-10；命令与原case/hash/用户句/资源预算不变，无新增CLI/framework。旧无approval版本不可首跑。主审核实际终态packet前不判原gold通过；非白名单/未知等待或证据不全如实保留执行失败/观测缺口，不改变分母。

