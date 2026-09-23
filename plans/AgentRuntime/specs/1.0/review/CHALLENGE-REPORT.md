# 四视角对抗自审：ARP-EXEC-1.0

类型：**同一作者的四视角自审**，不是独立子代理，也不是生产代码独立审查。下面是已实际检查并纳入正文/代码的修订；“规格已裁定”不等于本地SDK通过。最终真实独立审阅见INDEPENDENT-REVIEW。

| ID | 视角/反例 | 采取的修订 | 验收映射 |
|---|---|---|---|
| Q01 | 实施：profile读者有了，首次writer/来源呢？ | 原authenticated factory＋实际activation receipt，sameUOW bind；不从env签权限 | R01/I07 |
| Q02 | 合同：缺ARP binding当legacy，损坏即可绕guard | 独立immutable arp_agent_protocols；真实升级已有行才LEGACY_AT_MIGRATION；无marker拒绝 | I07 |
| Q03 | 实施：用户更新context设置，旧profile不可改怎么办 | append-only arp_context_policy_adoptions；manifest effectivepolicy ref；旧request保持 | C08 |
| Q04 | 预算：512K全部当输入，漏tools/media/output | 输入/总窗交集、实际outputcap、完整wirecounter；未知上界拒绝 | C01/C06 |
| Q05 | 算法：向量与recent重叠，删除后又回填导致振荡 | protect suffix→一次candidate→重叠先删→只扩G不回填F | C03/C04 |
| Q06 | 协议：最近N条消息会切断toolcall/result | 原完整protocol group，mandatorycurrenttail；超大组不跳过 | C02/C05 |
| Q07 | 恢复：把AgentTurn当一个context，新toolresult无处进入 | 每Provider ordinal独立freeze；旧UNKNOWN不改payload | C07/T03 |
| Q08 | 索引：窗口退出就删原文，embedding失败丢历史 | 原Journal先持久；job/index为派生，可重建；高水位看空洞 | M02/M05 |
| Q09 | 检索：顶层搜全库，底层还裁最近2000 | 全partition词面/向量扫描＋局部pagination，partial不伪complete | M01/M04 |
| Q10 | 隔离：SessionRetriever支持传AgentId就越权 | model工具无AgentId，固定session access；Verifier仅自己session | M06/I03 |
| Q11 | 并发：embedding晚到在destroy后重新mkdir | FileGuard在root锁目录，最终写前查generation；DRAINING同锁 | R05/M08 |
| Q12 | 恢复：close/crash即unlink丢UNKNOWN与正式Review | close≠destroy；全部实际来源空+durablepin转移后purge | R03/R06/I04 |
| Q13 | 活性：Review永远保留就导致临时库永远不能删 | 仅live_temp_roots阻purge；durable独立根经receipt证明转移不删正式证据 | R07 |
| Q14 | 安全：tool readOnlyHint就可信；script绕Gateway | trustedadapter effectclass＋原policy；sandbox network/process限制；OPS只候选/明确意图 | T01/T05/T06 |
| Q15 | 合同：Skill loader又开SkillRun/TaskGraph | SkillUse只指原turn/call/reserve；workflow只原executor | K03/K08 |
| Q16 | 权限：Skill manifest里requestedtools自动获得权限 | intersection，每实际call当前复查；untrustedinstruction不覆盖A | K06/K07 |
| Q17 | 曝光：catalogue列出或tool返回就说Verifier见过 | 只能原实际Provider inputreceipt关联manifest/view | C09/I05 |
| Q18 | SQL：直接insertPURGED/delete重建/初始admitted | insert/update/delete/identity guards；recursive_triggersON；真实writer仍核来源 | R06/K04/I06 |
| Q19 | 跨库：index+execution一次事务想象；readonlySource临时hash | index先幂等commit后centralACK；原request和导入桥保留 | M02/I01 |
| Q20 | 交付：Schema嵌套复制漂移，单独包缺文件 | 唯一schema pointer330字段映射＋hashchecker＋完整manifestZIP | I06/交付检查 |
| Q21 | 活性：TG门禁没过就不许任何Runtime开发 | body先实现确定合同，未满足producer阻特定use，不冻结无关；TG不被默认ON解除 | BODY_WIRED |
| Q22 | 概念：Agent“销毁”等于磁盘/备份全擦除 | 仅临时partition和cache销毁，正式raw/账本按原retention；明确非secureerase | R08 |

## 四轮结论

1. 实施者视角：主合同/来源/函数/SQL/Host/toolDTO/测试映射均有明确指定；本地等价路径需要一次source-map，不声称读到了dirty字节。
2. 合同视角：没有新Task/效果/预算权威；每个新增side表属于execution或temporaryindex，跨库不宣称原子；当前权限与冻结选择分离。
3. 并发恢复视角：fence、FileGuard、任务幂等、原UNKNOWN、temporary删除与正式pins有完整顺序；实际不同OS/native仍须测试。
4. 测试变异视角：60组SDK/16定点变异有具体断言；纯规则、SQLfixture和参考变异执行结果见reports，不能自动替代生产门。

尚未证明的不是另一个未选架构方案：**本地真实调用边、可用embedding/tokencounter、实际migration和OS文件锁、原生Host、真实模型质量**，这些是本次实现/验收的交付对象。遇普通命名/registry等价复用直接处理；遇无法满足的身份或权限合同精确标记源缺口，不填默认业务值。
