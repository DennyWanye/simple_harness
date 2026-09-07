# 工具多消息生产接线：局部通过，空assistant仍待SDK修复

2026-09-06 successor status: the original M614 empty-assistant failure below is preserved. M615 installed combination passed22 tests+2 subtests on935d3e12. See [installed result](INSTALLED-0615.md).

最后更新2026-09-06。对应INTEGRATION契约已获Dirac只读限定ACCEPT；faa4c98f production源码及8项证据hash已获Dirac只读限定ACCEPT（非空完整组范围），不称整片完成。

新completed工具组：observer从真实Host effect身份index及SDK公开因果reader取得来源，terminal与全部child S1同租约/fence事务提交。v2独立UUID/marker，旧v1的两消息字节/重放不改；缺工具来源仍完整归档，不部分索引、不补旧source。每tool child带Host基于实际effect结算事实的版本化attestation；该凭据明确不是SDK独立ToolTerminalReceipt。读取按原terminal确定性核每child全部envelope/receipt，连同attestation重签外层校验和的伪造也被拒绝。group包括所有USER/assistant/tool，role/provenance、ordinal及tool parent来自actual source。

H073/M0614/S0313实际dynamic Host连续11turn，第一组跨两轮重复raw call ID的完整6item：两个tool对应不同internal call/effect与parent2/4；真正short非空命中、重开相同refs、遗忘tool来源后无hit。一个新写中断发生在assistant child之后、tool child写入前：terminal与所有child均回滚，恢复再次观察提交5child而非半组。两项重开故障验证missing child不修复，以及更改attestation result hash、重算attestation和所有S1外层hash后，普通read_evidence_pair通过而exact v2 source reader拒绝。5项旧v1邻居通过，包括不补旧终态、原子提交、缺child、无来源tool整组阻塞、真实11组召回。

批次范围：r1两通过（完整6item/原causal reader）、1失败（故障测试错期待drive_once吞异常）；只改测试期待真实raise后r2 atomic通过。r3新增missing/resigned两项及旧v1五项共7PASS10.01秒。不是三批累计一次全量。

**仍有真实红：r4空assistant完整tool组**。H073公共authorize_conversation_public_text接受空字符串，M0614 short_horizon.resolve_authorized_public_text却调用non_blank，注册时抛`ShortHorizonIndexError: authorized public text must be non-blank, bounded, and contain no NUL`。原消息和S1不填placeholder/不丢空父消息；完整验收测试保持失败，未skip/改为预期失败。已交Memory源码叶补合法空assistant及完整因果组契约，旧installed614不修改。本片尚未完成，不能据正控宣称所有真实tool消息可索引；非文本artifact组仍明确不可表示，继续处理。

所有批次145默认共享OS锁2GiB/180秒；无stop_reason/remaining成员/cleanup_error。没有模型/真实网络Provider/native，使用真实SDK运行栈加确定性Provider、实际Host工具路由/效果和store。新文件在用户主checkout未切换、未发布。

| 批次 | PGID | exit | 峰KiB | 资源秒 |
|---|---:|---:|---:|---:|
| r1 | 40626 | 1 | 205072 | 6.447 |
| r2 | 40719 | 0 | 178352 | 1.75 |
| r3 | 40850 | 0 | 200480 | 10.495 |
| r4 | 40972 | 1 | 215792 | 4.718 |

命令为主M614独立解释器，PYTHONPATH=backend/-B/pytest插件autoload禁用/pytest_asyncio；test_primary_tool_message_ingestion.py（r1首两项、r2原子项、r3两个tamper、r4空assistant参数）及r1 test_primary_tool_causality.py。r3旧邻居为test_primary_short_ingestion.py的test_old_terminal_is_not_backfilled、test_new_message_source_atomic_rollback_and_runtime_reopen、test_marked_missing_child_replay_rejects_without_repair、test_tool_group_blocked_whole_while_normal_groups_index、test_real_eleven_completed_groups_outbox_short_recall。各basetemp为同ignored根对应rN-db。新重放换新目录不覆盖失败。

| 本机ignored证据 | SHA256 |
|---|---|
| .local-test-evidence/2026-09-06/tool-message-v2/r1/command.log | e3c15a9dbc518a4ce2de1860749101b80c0e24f3c7bc9a134f2ca6ea5c7102d5 |
| .local-test-evidence/2026-09-06/tool-message-v2/r1/resource.json | 8db9cf0d2fcb369104b1d636aa02443efa35756ec6a3f99c3aafbac1c6807e73 |
| .local-test-evidence/2026-09-06/tool-message-v2/r2/command.log | e26d0fb4bfcf8119be91d7f72c31b12932a638789d2c1afb4c369d26a9a5393d |
| .local-test-evidence/2026-09-06/tool-message-v2/r2/resource.json | 7a7379270ba5e5418143b1dd83261088ac64cfc3f3ae6ccb09ab6667440e40bc |
| .local-test-evidence/2026-09-06/tool-message-v2/r3/command.log | 836baebf4404a43c43a9373cdb42ede16a583a1032cc54b3e48a7a57b47be1ef |
| .local-test-evidence/2026-09-06/tool-message-v2/r3/resource.json | 5dffdea128587281525f8c6221dbd5ff4180b2d794f37f28d6887bd9c7e07bd0 |
| .local-test-evidence/2026-09-06/tool-message-v2/r4/command.log | 1362a865aed3ab7f6a1b203627c5084bef590bf2fbff4fc81bb2188dd4d69d91 |
| .local-test-evidence/2026-09-06/tool-message-v2/r4/resource.json | 9ca23d74a590045ff60c2cb914b23ef67408dc16bc1f5448039c83ba1ecc52a3 |
