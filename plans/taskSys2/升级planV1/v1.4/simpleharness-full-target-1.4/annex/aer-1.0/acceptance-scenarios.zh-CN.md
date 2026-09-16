# AER 专项验收规范

以下 48 项均未在 SimpleHarness 执行。参考纯函数测试不关闭这些项目。

| ID | 场景 | 输入与触发 | 必须断言 | 原需求 |
|---|---|---|---|---|
| AER-V01 | 漏掉根要求 | 用户要求 Windows AND Linux；候选仅 Linux 有测试；提交 PASS Review | 缺 Windows 准则不得根 ACCEPT | R12, R28, R44 |
| AER-V02 | OR 与硬门分离 | 结果允许证明 OR 反例；隐私是硬门；反例通过但隐私失败 | 总体拒绝，不被 OR 掩盖 | R10, R12, R28 |
| AER-V03 | 测试范围过宽 | 测试只覆盖样例输入；Claim声称所有输入和线程安全 | 无关扩大部分不能获得对应 assurance | R28, R29 |
| AER-V04 | 未执行检查 | 规定编译/测试/独立审阅；测试工具不可用而模型给 PASS | ERROR/UNKNOWN如实保存，不接受 | R28, R47 |
| AER-V05 | 独立性 | 候选由agent-A生产；用A的自评或Reviewer修改候选后提交 | 自批拒绝；修改产生新候选另行审阅 | R28, R50 |
| AER-V06 | 冻结产物 | Review绑定hash-H1；把同路径文件替换为H2 | hash不符；不能接纳H1 Review给H2 | R12, R16, R28 |
| AER-V07 | 动态取证 | ReviewPackage固定目标；Verifier获取新的受权工具证据 | 保存append和最终证据manifest，旧模型请求不改 | R28, R34, R51 |
| AER-V08 | 基础设施与判断分离 | 独立Reviewer请求失败；解析器生成默认REJECTED/PASS | 不得伪造语义结论；记录执行错误 | R28, R57 |
| AER-V09 | 局部完成与组合错误 | 两个孩子分别通过；输入schema/版本组合不兼容 | 组合Review失败，根不完成 | R12, R15 |
| AER-V10 | 有限报告 | 用户要求客观调查并列出未知项；确有未知外部主张 | 可接受披露合格的报告，不把未知主张变TRUE | R12, R28, R29 |
| AER-V11 | 要求修订复用 | r2只改变一个准则；旧Review迟到并复用无关子成果 | 旧Review不改；carry-forward显式绑定r2 | R14, R17, R44 |
| AER-V12 | 验收与发送分阶段 | 报告PASS且需要获批发送；报告提交时推进Mission | 可接受报告但根等待实际发送要求；不出现循环门 | R12, R28, R40 |
| AER-K01 | 未知不是假 | 命题无合法记录；查询其否定 | UNKNOWN；不得依absence生成FALSE | R06, R29 |
| AER-K02 | 多组理由 | K由(E1 AND A1) OR E2支持；撤回E1 | E2充分时K仍有支持，旧引用不改 | R29, R30 |
| AER-K03 | 来源相关性 | 10份材料转述同一来源；政策需要两个独立来源 | 不满足独立性；不得按数量计票 | R28, R29 |
| AER-K04 | 互相引用 | A支持B，B支持A，无anchor；运行闭包 | 都不产生有根TRUE | R29, R30 |
| AER-K05 | 带外部但不充分的循环 | A需要A AND E；E已知；运行闭包 | A仍UNKNOWN，不能只看SCC有入边 | R29 |
| AER-K06 | anchor撤回 | E→A→B→A曾为真；E撤回重新求值 | 派生循环不能自行维持TRUE | R29, R30 |
| AER-K07 | 正负冲突 | 同scope同时间有充分正/反证；方法要求该条件TRUE | CONFLICT阻止放行，不按多数覆盖 | R06, R29 |
| AER-K08 | 异步传播窗口 | 旧witness已缓存；提交来源撤回后暂停dirty worker | epoch不符的Scheduler/Context/action立即复核或阻断 | R16, R30, R34 |
| AER-K09 | 过期事件延迟 | witness.not_after到期；计时器暂未运行但工具准备handoff | 实时门拒绝；Replay历史结果不改 | R06, R30, R38 |
| AER-K10 | 用途时间语义 | 库存为START前提，操作合法消耗库存；验收时库存已变 | 不倒推开始非法；其他ACCEPT条件仍重查 | R05, R17, R40 |
| AER-K11 | 来源替换与原文引用 | 报告字面引用撤回E1；E2同结论；尝试改绑支持后直接发送原报告 | 引用合同仍失败；修报告、重审重批 | R17, R29, R30 |
| AER-K12 | 隐藏证据与权限 | scope外存在支持/反证；低权限Agent查询命题 | 不得泄露隐藏来源存在；缺权限不等于假 | R06, R34, R50 |
| AER-X01 | 相同命令丢回执 | 创建action已Commit；客户端重发同命令同内容 | 返回原回执；不新建操作/审批 | R03, R36, R40 |
| AER-X02 | 同操作不同参数 | 已有Operation O1冻结H1；同O1请求H2 | PAYLOAD_CONFLICT；不执行 | R03, R40, R57 |
| AER-X03 | 同目标两个合法意图 | 用户同Mission明确两次不同发生位置；相同connector/target提交 | 生成两个合法occurrence，各自授权，不误吞第二次 | R03, R40 |
| AER-X04 | Commit后未派发 | Outbox及命令回执已提交；强制退出/重启 | 同ID重发，execution去重接原工作 | R36, R38, R39 |
| AER-X05 | 应用成功丢回执 | 服务原子写目标与key；杀掉客户端响应接收 | lookup确认原效果，不重复写/发 | R38, R39, R40 |
| AER-X06 | 迟到请求空查询 | 旧请求仍在网络或队列；lookup暂时空 | 不能判NOT_APPLIED_FINAL；保持核对或有效同key去重 | R38, R40 |
| AER-X07 | 去重窗口过期 | UNKNOWN且服务key TTL已过；恢复并请求重试 | 禁止自动重发，不把长期OperationId当远端永久记忆 | R40, R60 |
| AER-X08 | 旧执行者和撤权 | 旧worker通过部分准备后暂停；撤权/新epoch，然后旧worker继续 | 未准入的请求拒绝；已外发只核对不假装撤回 | R16, R38, R50 |
| AER-X09 | 外部目标改变 | approval绑定目标version-v1；另一写者改到v2后请求执行 | 条件写失败/拒绝，不覆盖新值 | R16, R40, R50 |
| AER-X10 | 部分效果 | 批动作只完成一部分；返回PARTIAL/崩溃 | 已知部分保留，剩余与补偿独立计划，不整体重发 | R40, R41 |
| AER-X11 | 补偿冲突 | 原操作将A改B，别人改C；系统想回退A | 检查当前版本，不无条件覆盖C；补偿独立授权 | R41, R50 |
| AER-X12 | 晚到费用 | Mission已终态，原调用后来确认用量；重复导入usage receipt | 计入原责任一次；不复活内容任务/授权 | R25, R38, R39, R51 |
| AER-I01 | 改方法保留成果 | A/B路线共享已接受C；A前提失效切B | 保留C的有效引用，旧未知动作先核对，费用累计 | R11, R14, R25, R40 |
| AER-I02 | 并发反证与接受 | accept读取旧支持集合；另一事务增加反证 | 集合revision/epoch使旧提交复核或拒绝 | R16, R29, R30, R36 |
| AER-I03 | 无关变化不重做 | Review针对Task B；A分支计划有无关变化 | B局部read-set仍适用可接纳；不全盘失效 | R14, R16, R17 |
| AER-I04 | 验收后来源改版 | 报告接受但尚未执行批准动作；来源撤回 | hand-off发现stale阻断，旧批准不能批准新报告 | R17, R30, R40 |
| AER-I05 | 取消移交 | Mission取消但外部操作UNKNOWN；尝试直接终态删除队列 | 必须保留或确认移交RecoveryObligation及通知 | R38, R40, R42, R45 |
| AER-I06 | 根成功但备用执行未收敛 | OR一支成功，另一支仍有pending动作；请求Mission COMPLETED | 核对/安全接管责任，不留孤儿也不重复动作 | R10, R12, R38, R40 |
| AER-I07 | 多库恢复缺件 | manifest含execution/CAS与业务库；删除某CAS/落后一个库后恢复 | 标覆盖不全并禁副作用，不靠重跑掩盖 | R37, R38, R39, R60 |
| AER-I08 | 纯Replay | 有历史Review/Operation/APPLIED事件；删除可重建投影并重放 | 同业务投影；零模型/工具调用，旧lease不复活 | R37, R38 |
| AER-I09 | 恶意证据跨Agent | 工具文本含提升权限指令；经摘要/Claim/Blackboard进入Verifier | 仍是数据；不能造Receipt/权限/正式Claim | R28, R29, R34, R50 |
| AER-I10 | 公开UI状态 | 注入坏schema消息/断网重开；任务处于合法UNKNOWN/评审中 | 协议失败与业务UNKNOWN分开；显示旧画面stale | R45, R57, R58 |
| AER-I11 | 验证资源保护 | Worker想耗尽全预算；尚需最终Review和核对 | 保护尾额/停止新分支，不自动豁免验证 | R25, R26, R27, R28 |
| AER-I12 | 完整报告发送 | 两份资料→独立Review→来源修订→H2批准→丢回执；真实受控环境端到端执行和重启 | 引用/要求/操作/费用/Resolution/UI一致，无错误宣布完成 | R12, R17, R28, R30, R36, R39, R40, R45 |
