# 工作项、状态、锁序与恢复

## J1. 三种job的生产合同

| kind | producer / semantic key | 调用/费用 | result |
|---|---|---|---|
| INDEX | 原Journal closure事务；`session/index_generation/group_id/source_hash/view/chunker/embedding` | 原call key=`job_id/batch_ordinal/input_hash`，Agent原lifetime/所属Task用途预算 | IndexSnapshot/原embedding receipts/partition batch receipt |
| PURGE | 正式destroy事务；`session/control_generation/destroy_receipt_id` | 无新模型费用；核对原unknown/usage | 实际rename/delete/central PURGED回执 |
| BIND_IMPORT | 原Provider/Skill/CAS source receipt collector；`source_receipt_id/destination_kind/destination_owner` | 不重新调用模型；使用原receipt关联 | 原Assurance exposure/正式pin导入回执 |

SUMMARY不在本期job enum，不创建隐藏摘要调用。模型摘要以后必须显式policy/真实usage，不在此暗启。

每kind payload/result见Schema；budget_owner_ref不是余额，不得从body注入principal/actual reserve。INDEX的group source refs、resource version、generation与期限均写入payload，不用运行时latest替换。

## J2. claim→call→index→ACK

```text
execution UOW: PENDING -> LEASED(row_version+1, owner, deadline)
无锁：查原 invocation id，若SUCCEEDED使用原输出；若UNKNOWN只reconcile，不再embed
execution UOW：真实call/reserve首次登记（仅不存在且合法）
无锁：bounded embedding worker执行；原结果/费用先持久
FileGuard -> execution短读source/state/gen -> partition transaction:
    append原批次commit_seq/group/chunks/vectors/receipt，同key异hash拒绝
释放全部partition/guard
execution UOW: 验证partition receipt → job DONE + 原ACK事件
```

崩溃边界：claim后未call可重新领取同job；call发送后结果未知不能新调用；结果入原ledger后未index复用原output；index成功未ACK按同batch receipt核对后ACK；cancel/destroy期间迟到输出先保留真实费用，再标job CANCELLED，不重建DB。不能在embedding线程使用主线程SQLite连接。

lease30s可renew；expiry只允许新协调owner查询原调用，不证明原计算终止。默认最大8次有意义协调重试、退避1/2/4/8/16/30s、累计5min（原policy更紧取小值），计数/期限不随重启归零。deadline后BLOCKED可由同owner管理命令retry：若原invocation UNKNOWN，只允许核对；真正新resource/epoch工作用新semantic key并链接旧job，非自动重做旧收费调用。

原startup/tick处理CREATING+due jobs，单tick≤8，单Session≤2，owner轮转。shutdown只停止领取并等待已开始短事务；不删除逻辑Session。INDEX没有可用资金或资源→BLOCKED，不能影响后续撤权/close事件消费。

## J3. 文件与数据库锁序

排名固定：catalog owner guard(0)、Orch UOW(1)、SessionFileGuard(2)、exec UOW(3)、partition(4)。允许省略，不允许倒序/递归跨连接嵌套。慢IO/网络/embedding/script都在全部事务外。

Context准备：Orch读一致快照释放→catalog事实读取→FileGuard+exec校验Session→partition页读→全部释放→纯composer→原Orch→exec request/reserve复核。它不是一条跨库ACID。正式handoff中原Runtime重新核查当前权威来源与冻结内容；读取缓存不是长期授权。

PURGE先FileGuard再exec读取完整来源；若需要Orch结果，先锁外收集并在遵循Orch→FileGuard→exec的最后核对阶段复查，而不是持FileGuard回调Orch。GC同序；不能在exec UOW的callback再等待FileGuard。SQL查询maxpage结束后close cursor，不跨网络轮次保留handle。

## J4. 生命周期联合表

| 用户/系统动作 | 原Agent状态 | Session | 可继续的路径 |
|---|---|---|---|
| create | 原Run准备/未drive | CREATING | 初始化job/来源检查，不能Provider |
| init完成 | open/idle | ACTIVE | 正常submit/context/tools |
| Turn完成 | open/idle | ACTIVE | 下一输入；不删除 |
| cancel_turn | open/原Turn取消或核对 | ACTIVE | 其他合法下一Turn等原unknown收敛；管理read继续 |
| close(drain) | closing | ACTIVE | 已接收Turn在原预算继续；不接新输入 |
| close期限到/cancel式destroy | closing | DRAINING(gen+1) | collect/reconcile/usage/import；禁止新模型业务调用；Turn可直接原取消终态，无须模型回答 |
| 全量disposal允许 | closed | PURGING | 实际句柄关闭、rename/unlink、回执 |
| delete成功 | closed | PURGED | 非敏感tombstone；普通检索拒绝 |
| 同root索引损坏 | open但检索阻断 | QUARANTINED | 当前授权read原Journal，受控rebuild新index；不默认新root许可 |
| 旧备份新root | 原历史 | QUARANTINED | Assurance诊断/当前重新授权；不恢复旧执行权 |

Disposal集合：TURNS来自原Turn collector（任何queued/running/result_pending阻断）；CALLS/UNKNOWN来自原provider/tool执行账本；PENDING_IMPORTS来自原durable import＋BIND_IMPORT；INDEX_WRITERS来自全部INDEX（PURGE自身不阻断）及其原call；TEMP_ROOTS来自ARP roots+原GC根真实位置；READ_HANDLES来自持有排他FileGuard与原connection tracker的实际快照。每类complete=true/count=0才可能清空，不允许省略某类。

## J5. rename、重建和保留

marker是SessionMarker完整canonical hash，记录到中央creation receipt；路径只能从受信root + 系统session目录生成，resolve后检查仍在root。锁文件在root/locks，不在待删目录。

PURGING记录实际trash destination与rename receipt后删除；未写receipt崩溃需同时检查原目录/精确trash marker，二者都有或身份不符仍保持PURGING，写PurgeProgress.blocking（DUAL_DIRECTORY/MARKER_MISMATCH），不得进入可rebuild的QUARANTINED。禁止按前缀glob删其他Session。Windows busy返回FILE_BUSY，保持同destroy identity重试。15min仍busy/unknown记录一次人工接管，不把状态写PURGED。

同rootrebuild仅允许destroy_command_id与purge_progress均为null；认证IndexRebuildCommand→复制原Journal引用、构建新gen→验证coverage/source/view/resource→atomic publish；旧CURSOR_STALE。不得删除唯一源。跨root restore必须原Assurance gate先开当前读取权限才允许重建；index rebuild不签发执行权限。

retention许可由原管理/隐私命令产生：精确table whitelist/row key/body hash/current source/references/terminal proof，只有受信Store writer能提交permit。删除body binding前原GC检查formal roots；idempotency/业务财务/操作记录归原保留政策，不随ARP tombstone删。全部相关调用者在BODY_WIRED登记，包括index rebuild清理、Agent destroy、offline_backup.restore_offline、Artifact/CAS GC、Host下载以及原session_history读者。

`indexed_groups.vector_ready_count`只描述该不可变物化批次当时的诊断数量。每次index_snapshot的真实vector_ready_groups从当前gen中受upper_commit约束的session_vectors完整计数获得；不得把初次为0的诊断列当永久覆盖状态。

## J6. 删除隔离与持久恢复（R1；替代所有“删除异常→QUARANTINED”旧描述）

唯一选择：**DRAINING/PURGING保留状态，typed blocking记录异常**。QUARANTINED仅表示尚未进入destroy的索引/恢复隔离。普通rebuild、restore重新授权、settings、API或手工SQL状态更新，都不能把持有destroy identity的Session恢复ACTIVE。

原destroy命令进入DRAINING时，同一exec UOW预生成精确trash相对路径及PurgeProgress、原destroy receipt。没有开始删除也必须保存同一destroy_command_id/hash，generation仅第一次fence增加。后续inspection/block/清除block/rename/finish只增row_version，不换generation或销毁身份。

PurgeProgress是中央arp_agent_sessions.purge_progress_json/hash唯一正文；SessionView引用同一内容。完整字段见Schema。source/trash相对路径由系统root/Session/原destroy identity生成，永不接受任意用户路径。expected_marker_hash绑定真正删除前的原目录marker：它可能带创建时control generation，不能拿fence后generation冒充新marker。marker字节与其生成receipt必须可读、精确匹配，不能自动重写。

状态与phase：DRAINING/DRAINING；PURGING/RENAME_PENDING→RENAMED→DELETE_CONFIRMED；PURGED/DELETE_CONFIRMED。state到PURGING前必须已有完整disposal proof。blocking不改变state/phase；FILE_BUSY才允许同destroy有界退避，其余异常MANUAL。原15分钟报警不释放责任、不声称删除成功。

| 实际检查（同FileGuard，精确两目录） | 已持久phase | 唯一动作 |
|---|---|---|
| source匹配，trash不存在 | RENAME_PENDING | 对精确source进行原子rename；产生真实rename receipt |
| source不存在，trash匹配 | RENAME_PENDING | rename后receipt前退出的恢复：记录真实RECOVERED_RENAME_OBSERVATION，再变RENAMED；不补造旧执行receipt |
| source不存在，trash匹配 | RENAMED | 验证同marker及disposal，删除精确trash；最后删除marker/目录 |
| 两目录都不存在 | RENAMED且有有效rename receipt | 删除后回执前退出：在受信root排他检查中记录真实absence receipt，再DELETE_CONFIRMED |
| 两目录都不存在 | DELETE_CONFIRMED且receipt真实可读 | 同destroy finalize→PURGED（ACK丢失幂等） |
| 两目录都存在、任一marker不符 | 任意删除阶段 | 不移动/不删任何目录；写DUAL_DIRECTORY或MARKER_MISMATCH+inspection receipt，保持fence |
| 两目录都不存在但无rename证明，或source在RENAMED后重新出现 | 任意 | DELETE_STATE_AMBIGUOUS；保持fence，不能凭路径缺失宣称purged |

rename/delete/检查事实使用原文件操作/CommitReceipt recorder；IO在全部SQL事务外但持有适当FileGuard。原fee/UNKNOWN事实从原ledger核对，不能由目录状态推断。安全模型仅覆盖管理中的root；不宣称安全擦除或抵抗任意本机文件篡改。

人工恢复入口：原认证`destroy`管理命令重送同destroy identity（或同ID的resume管理动作）→重新检查原两路径/marker及真实disposal→匹配才清blocking并继续原phase。没有“忽略marker mismatch”参数。管理员在本工具外纠正重复目录后也须重新取得精确inspection receipt，工具不替其选择删哪份。无法消除歧义继续BLOCKED，不要求用户再选一个新架构。

源码allowlist：原SessionLifecycleService/RuntimeRetentionService、原PURGE job消费者、SessionView projector、中央ARP schema、同事件writer和rebuild gate。状态事件沿`RuntimeSessionStateChanged`，from_state可等于to_state，body新增purge_progress_hash；dedupe仍session+row_version。所有原调用收费/正式pin职责不变。

## J7. 自动Recall的协调与destroy

原resume_pending附带扫描arp_context_recalls的非终态行（每tick沿原8项/每Session2项上限）；不是第四种新job、更不是新调度器。destroy/取消使未完成recall STALE并拒绝后续page；已发embedding照原ledger核对。disposal的PENDING_IMPORTS/READ_HANDLES/INDEX_WRITERS完整集合同时覆盖recall协调及其原call，原UNKNOWN不能被SKIPPED结果隐藏。临时cursor/page可清理，已冻结Provider输入与中央aggregate沿原保留根。

删除回执已持久但最终PURGED未提交时，双目录/marker异常仍在PURGING/DELETE_CONFIRMED上保存blocking；不得改写原rename/delete receipt。只有新受信检查证明原精确路径安全、且blocking清除，才能最终PURGED。DRAINING→PURGING必须从RENAME_PENDING开始，不允许凭空跳到DELETE_CONFIRMED。
