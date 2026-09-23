# 两库迁移接线

- execution的生产runner=`simple_harness/execution/sqlite/database.py`与其原schema descriptor/迁移入口；精确函数由当前父候选盘点。不得使用编排Store runner注册execution表。
- orchestrator的`agent_orchestrator/storage/schema.py`在本交付不增加业务DDL。原TaskGraph25、HTN23/24只是该库的来源报告，与execution迁移号无关联。
- index_partition schema v2仅在新的逻辑session/派生重建文件通过原partition owner创建；旧index可重建但必须留原Journal和旧中央请求事实。
- 本SQL是新ARP1.1.1 fresh additive descriptor；如真实父候选已应用ARP1.0或1.1，必须生成单独变更迁移，不修改旧描述符，不把CREATE TABLE直接重复运行。该分支由安装清单确定，不是重新裁定业务语义。
- PRAGMA先于BEGIN：foreign_keys=ON、recursive_triggers=ON，读回值。WAL由原Database policy决定，同本机filesystem；每个writer/recovery连接都验证，不在业务事务中临时改PRAGMA。
- `_apply_migration`完整语句边界按已裁定helper处理，禁止split(';')/executescript。DDL无BEGIN/COMMIT，由真实runner外层事务拥有。
- BEFORE INSERT identity guards额外阻止REPLACE；因此FK/recursive设置和Store去重都不能省略。retention permits只给删除许可，不给覆盖/复活许可。
- 真实完整schema隔离副本验：new/create、upgrade已有记录、trigger smoke、incomplete tail、错误整体rollback、foreign_key_check、checksum篡改拒绝、legacy行hash保持、reopen幂等、旧ARP marker保留。reference测试父表不是这份验收。

R1扩展arp_agent_sessions的purge_progress_json/hash并更新触发器；R3增加arp_context_recalls且在分区query/cursor purpose内加入CONTEXT_RECALL。旧descriptor/checksum一律不改；已有旧Session是否可迁移必须按真实destroy/source receipt重建进度，没有来源的删除中Session保持受控阻断，不用空进度把它复活。新marker=ARP_V1_1_1，旧ARP_V1_1仍按原冻结协议解释。新Request/manifest/receipt绑定v2 reader；生产迁移失败原事务rollback，不能清库套fresh DDL。
