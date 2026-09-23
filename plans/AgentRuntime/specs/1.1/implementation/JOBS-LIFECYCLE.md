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

PURGING记录实际trash destination与rename receipt后删除；未写receipt崩溃需同时检查原目录/精确trash marker，二者都有或身份不符QUARANTINED。禁止按前缀glob删其他Session。Windows busy返回FILE_BUSY，保持同destroy identity重试。15min仍busy/unknown记录一次人工接管，不把状态写PURGED。

同rootrebuild：认证IndexRebuildCommand→复制原Journal引用、构建新gen→验证coverage/source/view/resource→atomic publish；旧CURSOR_STALE。不得删除唯一源。跨root restore必须原Assurance gate先开当前读取权限才允许重建；index rebuild不签发执行权限。

retention许可由原管理/隐私命令产生：精确table whitelist/row key/body hash/current source/references/terminal proof，只有受信Store writer能提交permit。删除body binding前原GC检查formal roots；idempotency/业务财务/操作记录归原保留政策，不随ARP tombstone删。全部相关调用者在BODY_WIRED登记，包括index rebuild清理、Agent destroy、offline_backup.restore_offline、Artifact/CAS GC、Host下载以及原session_history读者。

`indexed_groups.vector_ready_count`只描述该不可变物化批次当时的诊断数量。每次index_snapshot的真实vector_ready_groups从当前gen中受upper_commit约束的session_vectors完整计数获得；不得把初次为0的诊断列当永久覆盖状态。
