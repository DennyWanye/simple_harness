# C01 未跑case静态候选清单

2026-09-07，只读主固定324aa613的runner/setup/输入字段及既有setup结果；不读gold、不编译运行、不修改主树。此清单不是模型质量结论，也不表示16条都已完成当前安装组合的runtime执行。

## 数量与排除

20条中：C01-10已有真实FAIL且不重跑；C01-13正在主resource-r2/scoring-r2首次执行，此处不预判结果；C01-06/11继续BLOCKED；其余16条为首次执行候选。

| ID | 输入适配主题 | 当前额外限制 |
|---|---|---|
| C01-01 | 按已存篇幅习惯改写更新说明 | 周三是原文材料，不要求计算日期 |
| C01-02 | 查询个人技术说明的语言要求 | 无新增输入字段 |
| C01-03 | 按既定规则展示给定读数 | 输入数值已提供，不需外部数值来源 |
| C01-04 | 按既定格式表达明确日期 | 原USER明确2026年9月6日，不依赖“今天” |
| C01-05 | 内部进度说明的格式 | 用途已在原USER明示 |
| C01-07 | 依照结尾约定拟一句回复 | 仅生成文本 |
| C01-08 | 手机阅读条件下整理内容 | 当前使用条件已在原USER明示 |
| C01-09 | 查询距离显示单位 | 无单位换算或外部请求 |
| C01-12 | 查询本人允许的工作联系时段 | 不创建日程、不计算当前是否营业 |
| C01-14 | 查询个人日志默认时区 | 不换算当前时间 |
| C01-15 | 查询采购比较显示币种 | 原USER明确只问显示口径，不查汇率 |
| C01-16 | 查询摘要的内容顺序 | 无新增输入字段 |
| C01-17 | 依照段落约定拟通知，先不发送 | “明天交材料”作为原引文；不扩为定时发送。setup含未到期提醒干扰，保留原公共创建 |
| C01-18 | 按短信渠道规则整理原文字 | 周四是待整理材料，不安排任务 |
| C01-19 | 查询既定解释顺序 | 无新增输入字段 |
| C01-20 | 查询两种报告的条数约定 | 单轮双事实查询，无脚本后续轮 |

以上原输入均为单条USER，自足条件来自原provider_input。使用现编译器的显式current_user_message/recent_messages/scenario_clock字段；本组不需要C05脚本轮或C07历史数组适配。未复制gold、期望类型或答案到本清单。

## 真实共用前置与不可外推处

- 既有C01-BATCH记录18个非revision新setup通过，加此前C01-10；C01-06另有公共revision正向，但当前FixtureSetupExecutor显式拒绝06，所以不能因为旧revision绿绕过runner阻塞。
- C01-11要求今天的文件名。Provider可信日期仍未接到该runner，显式排除；不把setup clock透传误当三端可信日期闭合。
- 候选16条均在现SETUPS/SPECS与FixtureSetupExecutor覆盖范围。真正运行仍逐例要求精确setup hash、public ingest/分析APPLIED/public graph回读、fixture manager关闭后生产manager重开；失败保留该ID，不静默跳过。
- 未定日期的episode沿既有clock−24h默认；未到期prospective沿clock+24h默认；共同fixture clock保持2026-09-06T10:00:00+08:00。这些是原setup实现约定，不从gold/当前机器时间生成。
- 审批驱动只允许公开exact context_route/memory_standalone。其他工具/其他route、未知Provider等待会BLOCKED，不自动批准发送、建任务、提醒或写文件。模型选择超出范围属于实际执行结果，不改提示或case替其过关。
- main生产factory/原PERSONA、实际Provider参数及公共审计照旧。每例独立userdata/Run/worker；原USER不改。评分只在worker退出后读取原oracle；类型命中不能替代整题语义审阅。

## 批量调度约束

当前CLI接受重复--case并逐child串行执行，但外部resource runner的180s是整个父批次总时限，没有每例独立deadline。不能把16个ID塞进单个180s批次而宣称每例预算180s。

可由主在同一默认锁规则下顺序调度各单例（或明确预算的有限小批），每次用新的resource/scoring目录；维持2GiB/默认磁盘准入、无自动case重试。本清单不创建新调度框架、不发执行命令，也不调整原质量分母/阈值。

候选ID：C01-01、02、03、04、05、07、08、09、12、14、15、16、17、18、19、20。

## 查阅依据

主backend/deskpet/quality/corpus_scoring_session.py（字段限制/06与11阻塞/生产初始化），corpus_c01.py（setup独立字节映射与种类），corpus_setup_jobs.py（实际APPLIED流程），corpus_scoring.py（串行child及评分时机），corpus_approval.py（精确白名单）；plans/2026-09-06-corpus-public-seed/C01-BATCH.md（旧setup证据范围）。原01-exact.md仅筛取ID标题与provider_input行，未展示或使用gold字段。

