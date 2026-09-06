# Procedure 后继：实际使用、Scope 观察与适用性

最后更新：2026-09-06。接续创建判别和M618普通失败恢复；本页是源码调查后的实施边界，不是已实现／通过的声明。只读核对主候选059cfca7；主树未改，M618固定后不混入新SDK改动。

## 已确认的真实接缝

| 事实与位置 | 后继行为 |
|---|---|
| Host `memory/primary_message_v2.py::pairs/verify/tool_link` 保存并验证实际工具消息ordinal、parent、Run、effect、终态回执 | 可用作工具因果证据，不自动表示运行了某个Procedure；完整注册工具组及Scope必须一致 |
| Host `sdk_adapters/tools.py::_validate_execution_identity` 对实际executor与Run冻结spec核execution identity/schema；`tool_authority.py::_FrozenCapabilitySpec` 有version/schema/effect权限元数据 | 复用实际工具快照构造applicability，不能采用模型填报的版本、hash、风险；环境还需绑定实际workspace/runtime身份 |
| Memory `core/manager.py::record_procedure_observation` 已有公开消费；`backends/sqlite_v5.py::_verify_procedure_evidence_unlocked` 必须精确命中Scope证据和conversation registration的tool causal回执 | Host须持久真实观察authority并向builder注入resolver；重开重放原ref，不能临时签另一个Run或补造注册 |
| Harness公开 `ProcedureObservationIntent` 要求target revision、transition_from/to、applicability、Scope、evidence span、terminal回执、risk/hazard与attribution | model只可提出使用目标／步骤，不能自授observation或effect；真实用途绑定必须在工具执行之前，成功之后才可归因 |
| Memory `record_procedure_observation` 在事务内按qualification epoch、90天窗口和实际observation计算transition，并精确比对intent.transition_to | 不能让Host根据自己记得的“第几次”猜结果，跨进程／窗口滑动尤其不成立 |
| Host `human_memory_v7.py::typed_recall` 当前未提供Procedure applicability fingerprints | ACTIVE不等于已适用或可执行；仅往recall填静态“general”也不能闭合使用前检查 |

## 最小连续实施边界

1. **持久使用绑定**：为明确选择的exact Procedure revision记录subject、Scope、run、步骤与实际tool call绑定、工具／环境快照。入口走现有authenticated control或主模型结构化选择后的Host验证；不是从召回返回片段、聊天“成功”、Run completed推断使用。schema须由主协调分配，新表只追加，旧事实不重写。第一叶应限制可明确归因的完整步骤执行；未执行／失败／无法判定的步骤不能当完整成功。
2. **使用前检查**：用当前实际工具注册及workspace/runtime身份构造公开 `ProcedureApplicabilityContext`；与绑定revision的可信快照比较，drift阻止自动应用。实际执行前再核同一快照，沿现有权限、writer fence和物理dispatch，不因记忆ACTIVE绕过确认。
3. **终态观察**：仅从上述实际application binding＋公开工具终态来源＋完整已注册消息组取得Scope/evidence/回执，持久唯一observation ref。调用SDK公开消费，重开／lost ACK复用同ref；三个不同Scope资格由SDK原算法裁决，高风险不自动激活。

三步要形成真实生产链；独立resolver单测或一个永久拒绝的包装模块不能作为完成。

## 需要先补齐的公开事实边界

现有 `get_memory_mutation_receipt_view` 提供原mutation的exact operation绑定，`ProcedureObservationApplyResult` 提供某次历史观察的结果；它们都不等于当前revision、qualification epoch和滚动窗口的决策。已检公开manager/port，未找到适合给Host预备下一intent的exact Procedure当前状态／预期transition接口。Digital Twin为display-only，不能倒用作authority；不读取SDK私有SQL、不用多次故意错误transition试探。

建议下一SDK小叶为**有界、公开的Procedure观察准备读取**：输入真实owner/scope、exact目标revision和authority-free观察事实，复用消费端同一个决策函数，返回绑定输入hash／当前revision／qualification状态的预期transition。返回不含grant；Host仍须以真实application与工具来源验证并签发原公开authority，SDK消费在原事务内再次核验。source变更／并发推进显式stale，不把预读结果升格为执行许可。该新增接口的签名应先由主与本agent固定，不混已冻结M618，也不改变原阈值／DDL／旧receipt。

最小风险控制：真实一Scope成功只draft、三个独立Scope同revision/applicability推进；同Scope重放及lost ACK不加数；无application binding/错误工具parent或回执不签观察；当前工具／环境drift后物理执行0；窗口滑动或并发revision推进不猜transition。复用SDK已绿资格算法，本轮后继只验证Host跨层及新增公开准备接缝。

owner建议：本agent继续Procedure独有source/resolver与必要SDK公开准备叶；`runtime/composition`和工具执行前fence涉及Hegel，`context_route`的结构化使用选择及schema版本由主协调。Prospective source/store/consumer不碰。上述协调是既有用户授权内的文件分工，不向用户再申请功能许可。
