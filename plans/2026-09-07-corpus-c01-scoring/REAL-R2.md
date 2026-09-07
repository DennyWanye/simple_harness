# C01-13 首次真实评分：FAIL

2026-09-07。Host `324aa613`，H079/M619/S0313；含生产个人来源指导与Workflow自然退场修复。新的原case，没有重跑C01-10。

原 gold：“读取A答检查点；不因通用词典或B改译法。”实际15次Provider请求、14次context_route，均因 `context_route_workspace_reuse_requires_create_new` 拒绝，没有读取A。最终如实说明取不到本人词表并请用户提供，原gold **FAIL**。首次请求选择semantic，后续逐渐扩大到四种类型；全部原提议保留：required类型提议命中1/1、extra types=3，但实际取得A失败，不能给该case语义通过或把类型提议当召回成功。完整业务终态COMPLETED；public trace COMPLETE、观测完整。

实际请求中的reuse_workspace_of依次出现字符串“null”“none”“unused”“dummy”、空格及全零串，expected_source_hash还出现全零值。它们不是JSON null，不能当作真实workspace/source授权，Host拒绝非create_new的reuse字段是有效防护。模型在重复失败后仍未提供合法请求。现公共错误只给通用Tool execution failed，不包含字段省略指引。

当前本地SDK与Host `_request_payload`保留原parameters（required只有route），没有设置strict也未补全required。**尚未证实**为何模型持续填无关字段；不能直接断言远端转换缺陷。下一步检查可选字段的明确无值契约及错误反馈，禁止把字符串占位符当有效source。后续只读核对确认：14个公开决定各allowed，28份approval文件是attempted/allowed两阶段，不是28个决定；SDK持久TerminationState跨授权保留。重复键是工具名+canonical arguments SHA，本次14个参数hash全不同，streak最大1。实际15轮<25、14工具<50、1<10，没有超限或恢复清零；变化参数的同类失败不受该重复键上限拦截。只纠正Host注释，不加新限额或重测。

## 实际退场与统计

这次worker/parent都自然退出并产出review-packet与batch，PG69877 exit0，176.565s，peak1117072KiB，remaining[]，cleanupnull，stop_reason=null，最低磁盘3370MiB。没有触发180s/RSS/磁盘门；证明前一评分退场挂起的修复在该真实Run生效，不代表全取消/并发路径已验。

15次实际Provider reported usage合计 input52410/output5747/total58157；不推导费用。14份route审计均rejected，实际提案/授权/失败记录逐个保存；不是14次业务成功。Host/SDK Run ID与r1相同，是独立隔离数据库的确定性本地ID，引用必须带case与证据目录，不能跨root去重合并。

240条：目前2条首次尝试（C01-10、C01-13），两条原gold均FAIL，0条通过；其余238条未实际评分。暂停继续消耗模型跑同一无效参数路径，修复后用下一未运行case验证，保留两条失败。

## 本机原始证据

| 相对路径 | SHA-256 |
|---|---|
| `.local-test-evidence/2026-09-07/corpus-c01-real/scoring-r2/C01-13/review-packet.json` | `99b75e64e583703b2b8d106caeee1dff3668bf2518436c6f2ca6a9e02ec75ece` |
| `.local-test-evidence/2026-09-07/corpus-c01-real/scoring-r2/C01-13/execution.json` | `00e77781778b3b6c1c913eda14e12f49b8d5fb1179172e9a533c81e425ca145c` |
| `.local-test-evidence/2026-09-07/corpus-c01-real/scoring-r2/C01-13/observation-trace.json` | `4e8b834bfa6ead2d75783a4b89dfb973fc4c198d7c88ecbc98b9f4078c949036` |
| `.local-test-evidence/2026-09-07/corpus-c01-real/scoring-r2/C01-13/observation-route_audit.json` | `47d66389b7c915602b0cd9f87e25aa507bcd6e6b91401a58b59ae923be84a11f` |
| `.local-test-evidence/2026-09-07/corpus-c01-real/scoring-r2/C01-13/observation-route_effects.json` | `c1ef58c5f6f6a1018b537f616aa3603a296433247343124f6804d20253367bec` |
| `.local-test-evidence/2026-09-07/corpus-c01-real/resource-r2/resource.json` | `80cebcd84121c0a6183277202603eaca2197af142512ec1089f6538b881efff3` |
