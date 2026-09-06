# C04 clock/lifecycle prerequisites and source implementation

2026-09-06 main coordination. All 20 original cases remain in the denominator;
no C04 implementation/execution/PASS is claimed. No input/gold is used as setup
authority. The following overrides ordinary undated fixture defaults.

- C04-15 scenario clock is 2026-09-30T18:00+08:00; other C04 cases use September 6.
- C04-07 event August 20 and actual ingestion September 5 are distinct clocks.
- C04-15 event16:00 and ingestion17:00 are distinct. Do not stamp both with the
  scenario clock or clock-24h. Source receipt/Memory backend ingestion must reflect
  the intended distinct actual preparation steps and preserve first admission.
- C04-10/11 event authority has not triggered. Do not fabricate an external signal.
- C04-12 needs actual public reschedule retaining the old superseded reminder;
  C04-17 needs actual public cancellation retaining the old reminder. Creating
  only a final pending record does not satisfy the setup.
- C04-06 requires ZoneInfo Europe/London offset at actual September 8 date.
- C04-20 source trigger is Shanghai only. Never insert a computed UTC answer into
  the initial Provider/system input.

Before implementing this category, preparation needs independent scenario,
source-event, and ingestion clocks plus public lifecycle action/receipt support.
Any date normalization must be from authored setup/clock and reproducible timezone
rules, never gold. Current C01 CREATE job fixture does not provide these lifecycle
interfaces. Source/runtime006a67dc delivery does not include C04.

## 2026-09-06 后继源码（NOT_RUN）

独立分支 `feat/corpus-c04-time-setup`，基于已审prepare最终docs e0e7d68c。
`corpus_c04.py` 仅从原04-time.md的setup/scenario_clock字段提取20条原字节与hash，
不读取provider_input/gold映射。20分母完整保留。主registry未改。

`TemporalSetupBatch` 明确scenario_time与ingestion_time；public payload保留发生时间。
07实际manager/Hostreceipt/analysis clock设9月5日12时（原入库只给日期，12时synthetic），
15设9月30日17时；完成真实SDK analysis后再单调推进到原scenario clock。
公共ingestion receipt.accepted_at必须精确匹配，不能仅改返回metadata。

原精度与统一fixture锚点：未写日时的普通日为12时、月份为15日12时、周范围为首日12时、
夜间为21时、完全未给日期的过去Episode为scenario-24h。这些时点写入synthetic标记，
原范围/精度保留在public payload，不能把锚点当原始事实或评分答案。明确日期/小时优先。
16 July/17 August沿此前已批准粗月规则；17旧出发提醒缺日期暂拟8月16日12时重建占位，
不是原文断言，真正cancel adapter尚未落地前该例不交可用manager。

06真实ZoneInfo解析Europe/London的9月8日，20仅构造原上海trigger，不向初始文本补UTC答案。
09提醒只给9月8日，12时为明确fixture时间占位，不能声称用户指定12时。

`open_c04_fixture` 已写18条initial CREATE实际public SDK job执行链，constructor-bound
fixture authority、真实APPLIED且ACCEPTED、精确public graph payload/hash回读；
manager在context退出关闭，不启模型或生产worker。10/11仅pending event型记忆：
trigger为显式未绑定fixture event namespace，无resolver、无signal、无外部publisher，
不是Host提醒触发验收。新测试对public apply_prospective_signal调用直接拒绝，避免误触发。
Host评分runtime clock尚未接此新clock，返回边界明确False，不声称240可运行。

12/17在完整prepare入口仍显式拒绝pending lifecycle implementation；不静态造最终pending/
rescheduled/cancelled骗过。12拟真实旧9/7pending→REVISE新9/9rescheduled并保留原revision/
receipt；原superseded如何映射旧schedule而非重写旧snapshot，已发Dirac窄审。
17将真实旧departure→CANCELLED并保留另一个refund pending；两项继续实施，非删格。

准备首批仅18个新public setup参数控（包含07/15实入库时间、06伦敦DST、10/11未触发、
20原上海来源），全部NOT_RUN。native占槽期间无resource动作；12/17待真实链闭合后只跑
其新增控制，不重跑旧C01/C02/C03或已审drain/prepare绿。
