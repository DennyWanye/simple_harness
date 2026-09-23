# 原生 Host / 模型工具往返合同（Q02/Q03/Q17/Q18）

本文件与 `contracts/host-verbs.json`、`model-tools.json` 一起是唯一传输表。不增加新的 HTTP 服务器。Host 现有认证命令入口仍用下划线 verb；请求体不能带 caller/tenant/scope authority。服务从固定 Principal、subject Agent 的真实归属、Session binding 得到这些信息。

## 1. Transport

`HostRequest` 所有字段必填：schema_version=1、verb、command_id、subject_id、expected_revision、cursor、limit、payload_ref、payload。
- command_id：写操作必需且非空；读操作用读请求 ID，不创建写回执。
- subject_id：本次 Agent ID；catalogue 级操作使用现有认证 project/owner 的固定 subject；不从 payload 跳到另一个 owner。
- `payload` 与 `payload_ref` **恰好一个非 null**。普通 UI 使用 inline payload（256KiB上限）。artifact-ref 模式只接受现有认证上传链的同 schema、同 owner 不可变 artifact，resolver 完整核验再严格 decode；不是任意文件路径。
- cursor/limit 在有分页的 payload 中也存在时必须逐值相同；无分页时 envelope.cursor=null；limit是协议上限，不改变已冻结 cursor 的查询。
- settings update：envelope.expected_revision = payload.expected_adoption_revision；不相等 CONTRACT reject。其余写操作分别是 Session.row_version 或 catalogue activation revision，不借用 policy.revision。
- 每个 verb 的 request/response 类型以 `host-verbs.json` 为准。`HostResponse.items` 是该 verb 的 **一个 response DTO**（数组一项）；CataloguePage/HistoryReadPage 自己管理页面items，不把业务条目再扁平化。
- `HostResponse.next_cursor` 与内层分页 DTO 的 next_cursor一致，非分页为null。error与成功items互斥。写成功command_receipt必有；读失败不制造写回执。view_revision只按表定义，不用全局随机计数。

## 2. 未发生第一次模型请求时

settings_get 必须可读到：profile/context policy准确ref、配置、adoption_revision=首次正式adoption的实际值、session_generation、activation/approval receipt。ContextSummary.context_id/manifest_ref/manifest_policy_ref/last_count_mode及实际请求计量=null；retrieval_status=null代表尚无请求，不是COMPLETE_EMPTY。configured_context_tokens和next_request_policy_ref仍来自有效adoption，不填0或猜默认配置。

原子 settings update只改变之后尚未冻结的请求。已经PREPARED的请求不会被重新序列化；安全撤权例外走取消未发送协议（主计划§请求矩阵）。重复command_id同body返回原receipt+原result_revision，不返回今天的最新结果冒充当时结果；需要最新配置再GET。

## 3. 一次完整 UI 往返

以下 ID/hash仅说明字段关系，`examples/host-settings-flow.json` 提供机器可编码夹具，不是实际授权。

1. `agent_context_settings_get`: payload={session_id:S1} → policy pin P7、adoption_revision=3、session_generation=1。
2. `agent_context_policy_submit`: payload={schema_version:1,policy:完整Policy,expected_policy_ref:P7}，command C1。权限判断由当前配置编辑权与policy边界决定。原批准writer canonical存CAS+arp_policy_objects，并产生原receipt；返回新P8+批准ref+CommandReceiptView。不是上传完就自动采用。
3. `agent_context_settings_update`: payload={schema_version:1,session_id:S1,candidate_policy_ref:P8,expected_effective_policy_ref:P7,expected_adoption_revision:3}；expected_revision=3，command C2。
4. 如果另一更新已使adoption=4：返回POLICY_CONFLICT，原C2无成功回执，不改变状态。GET得到P9/adoption4；UI明确重试时使用新command C3、P9/4、新候选或仍获准P8。
5. C3成功但断线：`agent_command_receipt_get` payload={command_id:C3}。读取当前caller读权后返回原命令hash、P8、adoption5、原receipt。相同C3重送同body同结果；不同body明确冲突。
6. Context详情：`agent_context_manifest` 用context_id读取固定Manifest，不能用当前P8重解释旧P7请求。分页字段从manifest固定内容取，cursor锁定context/hash/field/offset/owner。

View revision四轴：policy.revision=配置正文版本；adoption_revision=Agent选择哪个policy的次数；session_generation=控制/销毁fence；context selection revision=一次实际请求记录版本。任何一轴不得替代另一轴。

## 4. history search / read

模型工具不接收可自由切换的SessionId；传输适配器绑定当前Agent。管理Host允许在已认证Agent所属Session读取，cursor purpose与模型工具不同。由于SearchPage声明MODEL_SEARCH，管理 search 使用同搜索引擎但经ManagementSearchPage适配（对应新增schema，purpose=MANAGEMENT_SEARCH），不可直接重用模型token。

SearchRequest：query、cursor、limit、max_bytes 全必填；cursor非空时query/limit/max_bytes必须匹配原查询。SearchPage允许PROGRESS页items=[]、has_more=true，表示尚在扫描。只有phase=RESULTS且ranking_final=true才返回最终排名的分页。所有返回对象实际canonical JSON字节≤max_bytes；body_bytes自身计入（用有界反复计数直到定点）；不能仅算text字段。

HistoryReadRequest用inclusive seq_from/seq_to，冻结highwater。单record过大按UTF-8字符边界切片，slice_hash是所传字节，record_ref.hash是原完整record，不互换。返回seq已覆盖范围和隐藏记录范围；权限不允许暴露的记录不披露其敏感元数据，仅按获准policy表示缺口。cursor保存seq+byte offset+原snapshot，切片重放完全相同。最后一页has_more=false,next_cursor=null。范围非法/不在快照→HISTORY_BYTE_RANGE_INVALID，源暂不可读→JOURNAL_SOURCE_UNAVAILABLE，不返回空页假装成功。

分页读取不会预取全Journal进内存。每页最大64 slices，max_bytes≤65536；模型上限可按ToolDefinition更紧。cursor过期/撤权/控制代次变化/索引世代切换分别明确拒绝；索引同世代追加不破坏固定snapshot。

## 5. Catalogue：Summary、Details、执行三者分离

CatalogueSummary 单项≤4096 canonical bytes（name≤128、description≤1024）；一页≤65536、最多64项。完整Skill包可以1024文件/64MiB；不要求完整定义塞进summary。对800文件合法包，summary只给file_count,total_payload_bytes,definition_ref,detail_ref；`agent_skill_details` 精确文件页最多64项，page≤65536。超长单路径在import阶段受255字节/段和1024字节总路径政策限制，避免后续分页无法表达；这是本平台便携性政策不是Agent Skills通用要求。

SkillDetailsPage始终引用同一skill/bundle/dependency lock；页之间安装新版本不更换旧结果。旧版本内容仍可读但current_usable由当前activation/权限确定。实际文件内容走现有artifact.read的准确range接口，不一次返回8MiB进模型。

MODEL视图仅披露description、输入/输出schema、明确工具范围、可用性和可回读ref。不披露credential_ref_name、endpoint_namespace、安装物理路径、account ID。MANAGEMENT视图也只有拥有deployment-admin读权才可读取这些字段；无raw秘密。`current_usable=false` 不意味着定义不存在。

## 6. 实际 Host 接线

沿既有 `backend/deskpet/orchestration/{handlers,service,projection}.py` 和 `sdk_adapters/{sdk_candidate,capability_catalog,skill_resolver}.py`；Skill install沿 `capabilities/skill_install.py`。这是handoff/前序报告已定位的入口，后继Assurance字节需集成人盘点。

SDK `api/runtime_plane.py`（NEW）提供typed服务，Host handler只做DTO解码/固定caller委托，写入由原exec/registry owner承担。UI在真实Agent详情展示settings、manifest、history分页、Skill当前/历史、destroy进度；不能只在MissionsView新增一个不连Agent Runtime的假页面。开发记录实际组件路径。

事件只推非敏感 `{event_id,eventseq,event_type,subject_ref}`；页面重新认证后拉准确DTO。重连从原eventseq补读，重复通知不重复写。原Tauri负责启动backend/vite，隔离Host root/userdata/port，不双启动；精确candidate wheel/import hash必须实测。

错误由error-catalogue唯一表映射：422 contract、403权限（不存在同owner对象可统一404防探测）、409冲突/陈旧、503资源。WS结构保持Error DTO；不得把未知异常str含本机路径/参数透传。command replay仍检查当前读权，不因历史已批准而永久可披露。

## 7. R1–R5的局部传输变化

SessionView/SearchPage/ManagementSearchPage/ContextSummaryView和它们使用的RetrievalReceipt为v2；外层HostRequest/HostResponse仍v1，按原verb选择严格嵌套DTO。旧归档不能通过missing-field默认值转成新view，必须显式legacy reader，且不声称缺失的coverage已证明。模型工具SearchRequest没有增加highwater/权限/policy参数；CONTEXT_RECALL purpose不向模型暴露。

新增`agent_session_destroy_resume`：payload=SessionDestroyResume，携带session/agent、expected_generation、expected_row_version、原destroy_command_id/hash与**本次重新检查命令**command_id。固定caller必须仍有对应删除/保留管理权限；subject_id=agent_id，envelope.expected_revision=expected_row_version，envelope.command_id=payload.command_id。它委托RuntimeRetentionService.resume_purge，并写新inspection/control receipt；原destroy id/hash与目录marker不改。没有路径参数、override=true、ignore_marker字段。重复同resume命令返回原receipt，用户需要再次检查则新resume command但仍原destroy identity。来源不符或双目录继续显示blocking；不得自动删除一方来消歧。旧destroy命令重送只查原命令回执，不伪装成新检查。

Settings对rows>256/cursors>16/page_ms>500/query_ms>30000执行严格拒绝，审批/写adoption不落库；GET显示实际获准值，不秘密clamp。候选存在但F被裁成0时，ContextSummary展示candidate_count与selected_count分别为n和0，不显示“查询为空”。
