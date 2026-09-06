# 非本人用途的本轮输入来源（实施契约）

2026-09-06。Host base 0bf324ff；Memory base d8d80d5c（0.6.18）。冻结 wheel、旧 S1/hash/turn 不变。实施中，未宣称运行链完成。

## 边界与复用

所有240例使用共同政策：`simple-harness-memory-sdk/scripts/corpus_trusted_bindings/逐例绑定.md` §共同政策。原文 UTF-8 SHA256 为 `3963adb81d62aa5b64e95c6a7f1a4fb6d6dd76ce4390cb1ac4df9b72c0f6ed69`；该 hash 只是政策身份，不是授权。不读 gold/隐藏答案，不由聊天改变政策。

复用真实 signed-control request scope、S1 envelope/receipt、append-only foreground_turns 和 disclosure config。EvidenceItemAuthority/AdmittedEvidenceAuthority 证明来源和分类；HistorySourceOrigin 证明实际入场顺序，均不等于用途许可。既有 Host evidence resolver 复制请求的 item 字段，不能用于本入口签发分类。

首版仅支持实际本轮 USER S1 的完整 `/text`，`item_ordinal=1`、item_id=真实 delivery_key、USER/AUTHENTICATED_USER、identity UTF-8。认证控制 API 显式提交 `input_declaration={schema_version:1,kind:current_user|public_material,item_json_pointer:/text,text_sha256:SHA256(UTF8(text))}`。current_user 只证明模型本轮请求输入（PERSONAL 分类不变），不是给最终非SELF受众的正文披露授权；public_material 是完整本项显式公开声明。SDK 的 invocation_input_allowed 与最终正文可披露必须区分，不能将前者称通用 allowed。部分跨度、其它 pointer、旧 source、工具/助手/recall/short 一律不能借此许可。实际240中 current_user_message 与 trusted_setup.public_material 是两个独立项，不能拼成一个 PUBLIC /text；本片只验证单项，后续消费需分别绑定，不称240已接。后续两个独立项须逐项来源，不按相似文本推断。

## Host 原子事实

同一 S1+enqueue writer TX 验证 live authenticated snapshot、actual subject/primary、明确 disclosure_binding_ref 及原 current token。新 declaration 的来源必须在该 TX 首次入场，旧 S1 late-enqueue 不可升级。持久 turn schema 3，包含 schema2 的 atomic 标记和 `input_use`：完整声明、实际 S1/receipt hashes、主体/primary/delivery、控制 principal/authority/lease、原 disclosure token及实际 recipient/id/intended_audience/purpose、共同政策 hash。turn_hash 绑定它；input_use 不引用 turn_hash，避免循环。

旧 v1/v2 不回填；无声明的 SELF 路径字节不改，配置存在但未声明仍不能启用非SELF。相同 delivery 重放比较原事实，连接重建保留原 admission lease，不重新签发；换 text/kind/用途/token 均冲突。事务取消/故障不留半份证据。新 reader 重算 S1/turn/config/current head，不能仅因对象可构造就信任。origin reader 支持 schema3 的完整原事实，保留 atomic/legacy_before_only 原语义。

## SDK 与 physical 接线

新增独立 item-level current-input visibility 公共口；从注入的可信 Host authority 获取上述事实，核 exact item/S1/context/origin，复用原 suppression/lineage/同 snapshot 检查。普通 history 221/428、ordinary/candidate policy 不放宽；输入许可不流向旧历史/健康家庭/typed/short。Memory 不读 Host SQL，Host 不读 Memory SQL。不新增 Harness DTO。

Host 当前输入与保留历史分开检查。仅当当前 Run exact USER 项被新公共口证明可用才接受；原历史仍经原公共 reader，不用整 lane 可见替代。physical guard 检查实际请求来源/字节，慢 Memory 检查后再次比较原 token，G1→G2 必须拒绝而非替换。用途为非SELF时禁读/披露健康家庭记忆及其存在性/推断/别名/转交；配置或 public_material 声明不能重新分类旧记忆。

## 决定性 oracle（测试前固定）

1. 实际 signed-control + 新 S1 + exact 声明 + 非SELF config 原子持久；冷重开 exact 事实相同。无 live control、无声明、错hash/partial pointer、错主体/primary拒绝。
2. 同delivery重放不新增/不换lease；换文本/声明/原token冲突；旧S1晚排队不升级；after_evidence_insert故障回滚两者。
3. SDK 当前输入正向；旧健康家庭 history/typed/short 无此许可；伪 item/role/receipt/context、旧turn/foreign principal拒绝；原 source/memory suppression 及晚撤回继续拒绝。
4. actual Host physical 正向一次send；慢检查时 G1→G2、换request source或字节、源撤回均0send。继续保留既有 final original token fence。

分批只跑新失败/必要交互，不重复旧绿色。SDK 文件独立于 Singer Procedure 树，manager/port/root 小 hunk 后续合并；版本统一由主分配。首片 source facts 通过不代表 SDK/physical 已完成。
