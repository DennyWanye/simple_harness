# C01-13 同 Run 预算只读澄清

2026-09-07。观察对象：Host `324aa613`、H079，原 `scoring-r2/C01-13` 完整公开 trace。本次未执行模型、测试、SDK stack，未修改产品、数据库或原失败证据。

**结论：没有预算清零或超限事实。** 实际为 Provider **15<25**、工具 **14<50**、同名同参数连续重复 **1<10**。第15次 Provider 无工具调用并正常结束；原业务失败不能归因为预算恢复错误。

- Host Run：`877a6f33-15f8-5403-aec0-b0a6cfa2b007`。
- SDK Run：`product-sdk-7c7caaae53d8dae2c713dcb42fa62731e2b461c980b613ea40857c2c080f878d`。
- 15个独立 Provider request，ordinal连续1–15，全部succeeded、handoff_attempt=1、rehandoff_count=0。
- 前14次各提议一次context_route，实际effect ordinal连续1–14，全部以 `context_route_workspace_reuse_requires_create_new` 失败。
- 公开audit有14个独立allowed decision。28个approval文件是每个决定的RESPONSE_ATTEMPTED与ALLOWED两条记录，不是28次授权。
- trace标记COMPLETE、provider_observation_complete=true、operation_audit.truncated=false、coverage_gaps=[]；这些不等于本case业务通过。

## 准确语义与生命周期

固定Host `backend/main.py:8165` 的配置名是 `max_consecutive_same_tool=10`。H079 `runtime/drivers/react_loop.py:881` 的 `_repeat_key` 实际为工具名加规范JSON参数SHA-256；`runtime/termination.py:371` 按这个完整key累计streak。14次参数hash全部不同，实际最大streak=1；变化字段包括query、goal、reuse_workspace_of、memory_types、include_short_horizon。

Host `foreground_runtime.py:451` 的after_control只唤醒控制泵。SDK `react_loop.py:274` 每次读取同Run durable checkpoint，`termination.py:346/371` 在Provider/tool reservation前累计计数并CAS持久化；授权WAITING之后恢复原response/tool_batch，不以新driver对象初始化累计预算。公开trace的连续ordinal与此一致；本次未另开数据库私读来冒充公开checkpoint结果。

因此“同名工具”注释不足以准确描述现门槛；主负责纠正Host注释及REAL-R2未决表述。**不增加“同类错误10次”或仅同名工具门，不改SDK计数/限制/协议，也不把外层180秒资源deadline当生产预算。** 参数兼容修复继续独立聚焦。

## 可引用原始证据

根目录：`/Users/denny/projects/simple_harness-primary-candidate/.local-test-evidence/2026-09-07/corpus-c01-real/scoring-r2/C01-13/`。

| 文件 | 文件字节 SHA-256 |
|---|---|
| observation-trace.json | 4e8b834bfa6ead2d75783a4b89dfb973fc4c198d7c88ecbc98b9f4078c949036 |
| observation-route_effects.json | c1ef58c5f6f6a1018b537f616aa3603a296433247343124f6804d20253367bec |

同目录 `approval-001.json` 至 `approval-028.json` 为两阶段授权索引。SDK源码只读对象为主组合 `.local-test-evidence/2026-09-07/memory619-artifact/installed/simple_harness/` 的既有H079安装包；没有新安装或版本升级。原Manual可合分支保持 `7324a740`，本摘要仅独立文档提交。
