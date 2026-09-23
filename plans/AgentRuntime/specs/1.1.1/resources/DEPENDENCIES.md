# 有界资源盘点与装配（不是再次让用户选择架构）

安装/依赖处理属于本地已授权实施工作；本包不下载模型、不安装依赖、不读取凭据。`bindings.template.json` 的 null 表示尚未从实际集成环境核验，不是生产默认值。主集成人一次完成盘点，记录实际制品SHA和提供接口；缺哪项只阻断对应激活/验收。

| 资源 | 已选实现/原位置 | 必须冻结 | 缺失行为 |
|---|---|---|---|
| 原生聊天模型 | 当前批准DeepSeek应用profile，不改模型路由 | endpoint协议ID、能力/模板证据、部署ID，不公开endpoint/凭据 | MODEL_CONTEXT_UNSUPPORTED或SOURCE_UNAVAILABLE |
| 首计量器 | `runtime/deepseek_tokens.py::DeepSeekV41TokenEstimator`，HF备用仅相同真实模板 | 计量器完整代码hash、tokenizer每文件manifest、renderer/serializer hash；requires_prior_output_reserve与原预算记录对应 | TOKEN_COUNT_UNAVAILABLE/PRIOR_RESERVE_UNAVAILABLE；不使用bytes/2签上界 |
| 通用HF计量 | `runtime/hf_chat_tokens.py::HFChatTokenEstimator` | 原模型本地文件、chat template、实际服务序列化一致性 | 无服务等价证据不启用，环境依赖不要求重写模型 |
| embedding | `BAAI/bge-m3` dense，SentenceTransformer，本地CPU默认 | model实际commit/所有weights/tokenizer/modules/config的SHA清单、锁定库版本、dim1024、输入最大8192 tokens、SCALED_L2_F32_V1 | EMBEDDING_RESOURCE_MISSING；按activation/degrade真值表 |
| embedding使用路径 | `agents/memory/embedding.py::EmbeddingPort` 原sync接口适配 | shared pool资源fingerprint、sync offload worker数、原调用/usage adapter | 原purpose不足时增加原execution codec；不另造表 |
| SQL | 项目sqlite3、原exec Database/runner | sqlite_version、compile_options摘要、FK/recursive_triggers读回、FTS5 words/trigram能建 | SQL_PRAGMA_UNSUPPORTED/FTS_UNAVAILABLE；禁止假COMPLETE |
| Skill YAML | 项目锁内PyYAML安全自定义loader | 版本/lock hash、duplicate/alias/tag拒绝配置 | 当前锁无依赖先由正常依赖流程加入，不以简单split YAML替代 |
| Script | 原工具执行器批准runner | runner定义/实现hash、平台、shell=False、环境白名单、deadline、workspace政策 | RUNNER_UNAVAILABLE，不拼命令字符串 |
| Workflow | 既有冻结workflow executor | workflow ref+adapter hash+原checkpoint协议 | WORKFLOW_UNAVAILABLE；不新增引擎 |
| 文件锁 | 原FileGuard或按§锁序新增OS适配 | realpath、locks目录、进程/owner句柄行为 | LOCK_TIMEOUT/FILE_BUSY；不继承别平台测试 |
| 原生macOS | 用户当前Host源码隔离副本 | candidate wheel SHA、import路径/hash、Host commit+dirty、userdata+端口、Tauri owner | PENDING直到真实点击/日志；不能API测试代替 |
| Linux | 实施时登记已有获准Linux环境/机器 | OS/arch、Python lock、相同candidate wheel、FTS/locks/backend receipts | 未登记运行位置则ENVIRONMENT_DEPENDENCY；不擅自部署远端/容器 |
| Windows | 对应原生资源 | Windows FileGuard/subprocess/rename/unlink/恢复 | 未测保持PENDING，不由Linux PASS继承 |

## Meter最小适配验证（主体前仅这一小反例可执行）

固定三种真实原request：text、一次tool call/result、一次continuation携prior。调用原估计器的真实方法（在本地记录qualified signature），并通过提取的`measure_wire_and_prior`核对：old aggregate == wire+prior或old wire == wire（由接口明确选择）；P只减一次；serializer/tool restoration后hash稳定；更换response_format必须改变实际request身份。不是测试模型智力，不需要Provider网络调用。所有计数资源都必须真实存在；不存在记录名字/hash待补，不能用synthetic result代替这个验收。

## Embedding装配

主安装根的`NativeEmbeddingResourcePool`按(resource_fingerprint,device)缓存一份资源，所有execution pool通过受控客户端共享；最后一个Runtime shutdown关闭资源，但不删除Session。新Session创建只验证既有装配，不现场下载或执行model构建。

生产构造相当于：`SentenceTransformer(approved_local_path, device='cpu', local_files_only=True, trust_remote_code=False)`；`encode(texts, batch_size<=32, normalize_embeddings=False, convert_to_numpy=True)`后调用稳健f32归一化。参数兼容性以本地锁定库签名核对，不盲upgrade。索引和查询使用同一模型资源而非用户长期记忆DB。模型官方维度和输入上限仅是资源声明；不替代本地manifest。[W4]

默认required=true,degrade=true：首次资源缺失拒绝创建；运行中失败显式LEXICAL_ONLY。required=true,degrade=false：首次拒绝、运行中UNAVAILABLE。required=false,degrade=true：允许明确lexical激活；向量回来后以真实gen发布。false/false禁止。只有query服务失败而已有vectors时也不能计算semantic query；仍按同规则降级。服务恢复若fingerprint不同必须新gen，不混算旧向量。

本地CPU调用标NO_PROVIDER_CHARGE，cost_micros=0表示不存在外部Provider账单，不声称耗电/机器成本为零；真实input_tokens无可得计量则null。METERED必须原价格/usage来源，UNKNOWN保留hold。offload timeout不会杀死后台函数；原call保持在途直到真的结束，Late result先进入原ledger/费用，再因Session过期不写临时索引。

## 默认政策候选与结构夹具的区别

`default-policy.candidate.json` 是本版明确默认值，须经过正文规定的实际部署批准/激活回执才有权威；它不是已批准grant。512K只是用户配置上限。`examples/typed-fixtures.json` 使用另一份合法的384-token分块/200ms页预算/5分钟TTL测试配置，验证可配置性，不代表默认值。主体实现不得按夹具较小值暗改正式默认policy。原活动Agent不自动采用此候选。
