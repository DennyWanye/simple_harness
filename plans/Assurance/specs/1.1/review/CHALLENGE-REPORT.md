# ASSURANCE 1.1 对抗自审与接收方问题处置

**范围：**收到的F01–F15＋18个dirty源码摘录；活动正文、Schema、SQL、reference、字段映射、验收安排。四个视角均为同一作者自审，不是四个独立Agent。接收方计划评审与未来生产代码独立核验分开；后者NOT_RUN。

## 1. WorkAgent施工视角

- 挑战：同一业务已有scoped/root/composition/OCC，新增review owner会重复派发。处理：六行切换表保留builder/原writer，只换profile分支；同一hot-file集成人，详见F01/F05。
- 挑战：Ref叫execution但无真实producer。处理：专用内部kind＋真实原event/receipt桥；每种revision/hash和发行时点明确，六purpose结构样例。
- 挑战：reservation/TurnImported/ReviewBinding互相需要未来hash。处理：附录C.2/C.3固定source receipt先于引用，事件body不反向包含未来binding hash；已结算只阻新调用，不拒旧费用。
- 挑战：GC pin本身引用未来review。处理：先PREPARING，再真实同Missionreview BOUND；GC读原根＋pin，不知道全根时不删；不能假Attempt。
- 挑战：旧1.0 seed同时保留entries和另一份seams，会再次漂移。处理：seed.entries统一来自一份seams，check_plan对seed/integration内容相等做硬检查。

## 2. 合同与架构视角

- 挑战：evidence_ids只能靠字符串猜原版本。处理：review-key＋精确ref哈希标签、实际Provider披露receipt、批次链；旧raw重放不换目录。
- 挑战：bool检查混FAIL/ERROR、SEMANTIC没有生产正例。处理：CheckResult三值、批准CheckPolicy、OR-of-AND与完整9格组合真值表；真实local recorder，不造通用executor假回执。
- 挑战：多格式轮次与唯一dispatch冲突。处理：一个round/package、ordinal1/2子invocations；每次真实reserve，UNKNOWN不当重试。
- 挑战：旧judge直接完成Mission绕closeout。处理：提取当前末段为唯一locked finalizer；包括直接调用负例，通知采用明确pending通道而非假定万能outbox。
- 挑战：1.0缺binding即legacy会绕过。处理：独立持久creation lane，missing-new-binding失败；新Mission通过后同交付默认ON。

## 3. 并发、恢复与安全视角

- 挑战：t0备份不可能知道t1撤权。处理：managed restore的新隔离根，当前精确只读重新授权；无当前权威不披露，不假最新水位；不承诺对任意恶意磁盘回滚的检测。
- 挑战：源撤回先成功，异步recompute还没跑会放旧证书。处理：真实writer同事务Mission/global epoch，消费同步比较，完整集合/时间/权限核对；远端未导入事实不在本机保证中。
- 挑战：cursor原子性与事务外计算冲突、预算等待卡后续反证。处理：先事件→pending＋cursor同事务，prepare事务外，再效果/receipt＋ACK同事务；新目标seq使旧claim无权ACK。
- 挑战：time回退每tick无限增epoch。处理：持久clock_state区分首次进入ROLLBACK；高水位不降，恢复不延长原期限；expired协调lease不代表副作用结束。
- 挑战：SQL只guard UPDATE却允许直接终态/DELETE/REPLACE。处理：初始、删除、替换、同Mission BOUND guards；原三例和派生反例实测。Store仍负责真实性/字节/授权，不声称SQL包办。

## 4. 测试与验收视角

- 挑战：生成字段表复制nested schema且check只数数量。处理：common schema唯一词表，pointer＋展开hash，实际DDL列map和codeckind对照；人工nested enum/field mutation能被发现。
- 挑战：包有121测试却没覆盖上述例。处理：新增47项F反例reference测试，并保留旧121的有效断言，旧布尔fixture只在测试中显式归一化；生产oracle禁止bool输入。
- 挑战：12次记录不是模型通过规则，C08混机制与真模型。处理：C08仅late accounting，固定CSV/坏候选/受控真实service、4×3预登记oracle/上限/失败策略；不挑成功样本。
- 挑战：只写不能改共享Host却要求验原生，执行无法落地。处理：隔离Host/venv/userdata/ports＋固定wheel并验证import；三个underscore DTO与实际源路径明确，UI必须实际点击/重连/冷恢复。
- 挑战：完整验收被设成开工门，正文要求分片反复批测。处理：BODY_WIRED之前只阻塞microchecks，之后集中验收；架构/状态同交付回写，production test尚未跑不阻规格实现。

## 5. 结论与外部前置

F01–F15已在规格和附件中选择确定方案；生产SDK的普通符号定位、真实迁移runner/父键核对、保留dirty内容、当前profile/wheel识别由AS-0/实现者完成，不再因接口英文名不同重新申请架构。

仍不能从附件证明：用户当前全仓生产调用边已接好、真实外部服务具备何种能力、原生UI已加载候选、SDK/模型测试通过。对应条目保持PENDING。附件验证结果见VALIDATION，独立实现审查仍NOT_RUN。

**本报告不承诺零缺陷/100%实际可执行保证，也不会用再生成一份报告代替生产行为证据。**新发现只在实质语义矛盾时回PlanAgent；一般编码与已明确合同内的失败修复直接推进。

补充自审：本地checker在ERROR时可能没有任何assertion，Schema允许空输出但normalizer只能得UNKNOWN，不能为过Schema捏造PASS。包内review_package专用Ref及裸pin字段都具有精确解析表；Schema URI改为可离线注册的绝对命名空间，避免URN+相对引用歧义。
