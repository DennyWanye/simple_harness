# Host/模型工具协议与实际装配

本轮不重写 Host 登录、Service SDK 或桌面进程管理。固定系统身份由现有backend/service构造，绝不从wire读取principal/tenant/grant。候选SDK只装进隔离Host的原锁定环境，记录import/wheel/source fingerprints；共享dirtyHost不重装。

## 1. Transport

复用Host下划线verb dispatcher。`HostRequest`只是统一封套：schema_version=1、verb、command_id、subject_id、expected_revision、cursor、limit、payload_ref全部出现；下表null为显式无该用途，不是解析失败。GET无写command_id（null）；写操作command_id非空，scope与幂等都由服务绑定。limit1…100默认50由客户端显式发送。未知verb拒绝，错误响应`items=[]/error=Error`，成功`error=null`。

| verb | subject_id与输入约束 | HostResponse.items必须通过的精确DTO | 实际owner |
|---|---|---|---|
| agent_context_summary | AgentId；cursor=null、limit=1、payload=null | 单个ContextSummaryView，尚未有context时context_id/manifest_ref=null、NOT_REQUESTED＋NO_REQUEST_YET，不能造context | ContextProjection.read_summary→runtime exactmanifest＋当前读权 |
| agent_context_history | AgentId；cursor为有来源绑定的不透明分页token | ContextSummaryView数组，historical=true；next_cursor绑定Agent/root/readscope/paginghighwater | 原read侧projection；默认不返回正文 |
| agent_context_settings_update | AgentId；expected_revision=当前adoption revision；payload=ContextSettingsCommand exactartifact；cursor=null/limit1 | SessionView单个＋下一轮effectivepolicy在随后summary中展示；原命令回执通过既有Hostresponse关联 | 原 RuntimePlane settings command→same UOW appendadoption |
| agent_session_destroy | SessionId；expected_revision=当前generation；payload=SessionDelete；cursor=null/limit1 | SessionView单个。DRAINING/PURGING不是PURGED，blockers精确 | 原 authenticated owner→destroy service |
| agent_capabilities_list | AgentId；payload=null | CapabilityCatalogueItem（下述定义）数组 | 已获准CapabilityResolver只读snapshot |
| agent_tool_catalogue | AgentId；payload=null | ToolCatalogueItem数组 | 原Toolregistry/exposure只读 |
| agent_skills_list | AgentId；payload=null | SkillCatalogueItem数组 | 原SkillRegistry＋当前scope |
| agent_skill_install | 管理realm_id；payload_ref指原uploaded artifact ZIP；expected_revision=null；cursor=null/limit1 | SkillCatalogueItem单个、state=QUARANTINED | authenticated registrycommands install；不执行内容 |
| agent_skill_admit | 管理realm_id；payload=SkillAdmitCommand；expected_revision=activation.row_version | SkillCatalogueItem单个 | 检查originalevaluation＋真实caller后同txnregistry |
| agent_skill_suspend | 管理realm_id；payload=SkillSuspendCommand；expected_revision=activation.row_version | SkillCatalogueItem单个 | 原registry writer，撤销epoch，不删除旧definition |

如果本地Schema封套既有字段不同，新增独立`runtime_plane_v1` DTO adapter；不修改其它Mission verbs。业务级幂等receipt不得隐藏在error，走原response envelope既有receipt字段或以附带请求id精确查询。未获准跨用户对象返回统一notaccessible，不回实际usage/count。

## 2. Catalogue DTO 的完整字段（全部必填/无扩展）

本节在运行时是HostResponse.items的tagged validator。每项最多64KiB；最大100项，超总256KiB要分页，不截断字段。

```text
CapabilityCatalogueItem = {
 kind: "CAPABILITY",
 definition: Capability,
 state: "QUARANTINED"|"TRIAL"|"ADMITTED"|"SUSPENDED"|"RETIRED",
 state_revision: positive_int,
 current_usable: bool, reason_codes: tuple[str], registry_epoch: nonnegative_int
}
ToolCatalogueItem = {
 kind: "TOOL", definition: Tool,
 state: same_enum, state_revision:positive_int,
 exposed_in_context_id: str|null,
 current_usable:bool, reason_codes:tuple[str], registry_epoch:nonnegative_int
}
SkillCatalogueItem = {
 kind: "SKILL", definition: Skill,
 state:same_enum, state_revision:positive_int,
 evaluation_ref:Pin(kind=acceptance)|null,
 current_usable:bool, reason_codes:tuple[str], registry_epoch:nonnegative_int
}
```

definition是当前获准披露的registry完整metadata，不含Skill原文、脚本全文、secrets。exact未授权source不得出目录；目录查询失败返回SOURCE_UNAVAILABLE，不能伪装列表空。current_usable仅是当前预检，不是正式执行授权，每次handoff还要查。

分页token＝原Host opaque cursor机制，内容绑定 `{root,subject,queryhash,scopehash,registryepoch,sortkey,expiry}` 并使用原可信签名/HMAC；没有现成机制用服务端random cursor row/cache＋过期，不使用模型可改的base64裸偏移。失效返回CURSOR_STALE；UI重新拉目录，不覆盖历史context。

## 3. 给模型的动态工具，不等于Host管理命令

下列函数通过原ToolGateway注册，调用者固定当前session/request；接口内没有任意agent_id/authority字段。

| model tool | 输入（精确字段，无其他） | 输出与效果 |
|---|---|---|
| session_history.search | query:string≤4096字符，limit:int1..32，cursor:string|null | RetrievalReceipt＋有来源的RecallItem data views；当前session所有通道可降级但要标PARTIAL |
| session_history.read | seq_from:int≥1，seq_to:int≥seq_from，max_bytes:int1..262144 | 原Journal精确范围view+sourcehash+has_more。受session边界/权限限制；模型不可指定DB路径 |
| tool.discover | query:string≤2048，limit:int1..32 | 当前获准ToolCatalogueItem摘要；不执行、不自动曝光所有schema |
| tool.expose | tool_ref:Pin(kind=tool) | accepted snapshot-request receipt；下一个Provider请求才包含schema；当前请求调用未曝光工具拒绝 |
| skill.discover | query:string≤2048，limit:int1..32 | SkillCatalogueItem摘要，不返回完整SKILL.md |
| skill.load | skill_ref:Pin(kind=skill)，file_paths:tuple[str]≤16 | 当前准入bundle的指定instructions/references；不执行scripts，不扩大工具权限；实际消息进入下次请求才算曝光 |
| skill.execute | skill_ref:Pin(kind=skill)，arguments:JsonObject | 原ToolCall owner绑定SkillUse；INSTRUCTIONS返回引导内容，SCRIPT/WORKFLOW执行原executor；结果是ToolResultView，不自签Acceptance |
| skill.propose | candidate_artifact_ref:Pin(kind=artifact) | 存候选/送管理review；不能ADMITTED，不能安装依赖；原模型输出没有系统issuer |

tool/skill ref必须是实际当前模型已见目录中的精确ref，或刚通过上述discover实际返回并在下一Provider输入曝光的ref。目录查找命令自身可请求未知name文本，但不能据此推测某个私有ref存在。原模型工具调用身份保证同call重复不执行第二次。

首轮模型必须可见最小bootstrap工具schema：获准history/search/read、tool.discover/expose、skill.discover/load/execute。是否允许propose由既有rolepolicy；Verifier只具其获准只读版本，不能安装/执行写型script。每个schema都算入E，不因为“内置工具”免token。

## 4. 前端范围与真实验收

Host实际重点：`backend/deskpet/orchestration/handlers.py/service.py/projection.py`、原SDK adapter、`tauri-app/src/views/MissionsView.tsx`及其store。Agent自由聊天入口若另有store，复用真实已认证Agent命令入口，填source-map而非硬塞MissionId。不得将本计划新增verb假称已存在。

UI需呈现：configured/effective context、真实token计量模式、recent N、F recall大小与来源、索引lag/lexicaldegradation、工具/Skill确切版本、session关闭与物理删除进度。summary不默认包含raw历史；展开受单独读权。断线重连从原eventseq重建，历史视图不更新成latest，解析错误保留最后有效画面+报错，不Number(raw)||0。

原生固定流程：隔离Host→载入固定candidatewheel→创建ARP Agent→输入长历史/工具调用→点Context→调上限→确认旧manifest不变新request采用→加载Skill→模拟撤销→请求销毁→看DRAINING再PURGED→重连查tombstone。每步绑定实际event/input/request/hash和截图；HTTP脚本不是原生UI证据。
