# Skill / Catalogue 全生产闭环

本合同扩展既有Host managed资源writer，既有SDK runtime_catalog与冻结skill_resolver作为唯一读适配器。不是另外建一套“ARP已安装目录”。命令都从固定Principal/tenant管理入口进入；模型工具只discover/load/execute，无install/admit权限。

## 1. 导入

`import_skill_bundle(bundle_artifact_ref, scope_ref, command_id, caller)`：锁外精确读原artifact/CAS并核hash，做zip目录和大小校验，禁止symlink/hardlink/路径逃逸/大小写碰撞/重复entry/zip bomb。解包到新隔离staging，只读扫描不执行；UTF-8严格，hash使用原文件字节。native skill.json只从包内根读取，严格按Skill schema，`files`不含skill.json自身；否则自包含hash循环。实际未列文件拒绝，容许的平台记录文件只能由安装器显式白名单排除且写入receipt。

SKILL.md-only：有且只有一个根SKILL.md，frontmatter必须首行`---`，有结束`---`，≤16KiB；正文含frontmatter总≤128KiB。`yaml.SafeLoader`加构造器：mapping键必须string、同层重复拒绝；scan阶段拒AliasToken/AnchorToken/TagToken，拒`<<` merge；不支持隐式对象/日期类型。允许name/description/license/compatibility/metadata/allowed-tools，name/description必需string；metadata仅string→string；allowed-tools只做建议目录，不授权。name采用本平台`[a-z0-9]+(-[a-z0-9]+)*`长度1–64，等于目录中的规范技能名；该限制是ARP可移植政策。unknown执行元数据不推断SCRIPT。不凭scripts目录存在就执行。

两格式同时存在时，name/description/正文角色必须一致，否则SKILL_MANIFEST_CONFLICT。native的requirements/policies必须经实际注册ref解析；frontmatter不能覆盖它。SKILL.md-only转换为INSTRUCTIONS候选：系统分配registry identity/version，零执行权限，只保存真实来源和请求能力建议。import结果CAS+dependency lock候选+QUARANTINED记录+安装receipt在原catalog owner事务提交，cmd同body幂等，异body冲突；未提交staging孤儿交原GC。

## 2. 依赖闭包

`resolve_skill_dependencies(skill_ref, namespace_snapshot)`在已认证目录scope里解析exact ref；key=(kind,id,version,hash)，不动态latest。记录DependencyLock的nodes/edges/unresolved/complete/hash。允许未安装依赖，但仅QUARANTINED，返回DEPENDENCY_UNRESOLVED。节点≤128、深度≤16，无循环；同logical id两版本diamond冲突拒绝，不把不同source误去重。锁hash覆盖root skill、scope、全部有序nodes/edges/unresolved；不含自身lock_hash。

一旦complete lock用于试用，后续新依赖版本不改旧lock；升级需要新lock和评估。依赖suspend/retire使新调用不可用，即使root skill仍ADMITTED；原已发调用结果/费用按旧身份保存。没有任何依赖的Skill锁仍包含root精确ref、nodes可为空（root在独立字段），不能将解析失败当空lock。

## 3. TRIAL 的真实授权

`begin_trial(SkillTrialCommand, caller, command_id)`核验管理权限、current activation revision、完整lock和批准evaluation policy。由同管理命令产生原隔离TaskScope/workspace授权：subject是评价执行，root仅该新测试工作区、网络默认无、credentials仅测试目标的独立凭据引用，不继承用户生产账户。approval receipt必须记录policy/source和确切allowed tool refs。原eval task/Mission工厂及AgentBridge派发真实review/执行；在它自己的编排事务准备dispatch/预算，目录侧记录原durable intent关联，不声称目录库和编排库ACID。

首次builtin bootstrap：安装命令先批准静态Schema/内置只读实现；原deployment policy明确允许的内置工具可通过其既有检验receipt准入。不能为了启动验收Agent先临时AllowAll。第三方候选永远不沿builtin豁免；若基准工具未部署先记录ENV依赖，不用拟造通过的trial。

## 4. Eval / ADMIT 的最低政策

复用原Assurance CheckPolicy/EvaluationAcceptance，不另发一个技能自签PASS。默认skill-eval-v1的required checks为：bundle hash/path/schema/dependency lock、声明输入输出可处理、权限交集、效果路由不绕OPS、重放不重复副作用、错误/超时真实保留。INSTRUCTIONS验证只读装载/披露/no authority；SCRIPT增加真实runner/outputschema/exit0-invalidoutput反例；WORKFLOW仅当已部署原executor可测，无则UNAVAILABLE而非通过。

普通样例至少一个有效输入和每一项required check的负例；任何安全required check FAIL/UNKNOWN均不可ADMIT。特定能力质量需要原批准政策的holdout评估，不能用通用“正确率80%”覆盖未知语义。默认不要求以无关十几轮模型调用刷成功率；模型任务实际EvaluationAcceptance必须绑定policy/checks/输入/模型及scope。

`admit`核caller、exactskill/lock、official EvaluationAcceptance、原评价scope与策略currentness，原目录activation CAS→ADMITTED＋receipt＋semanticepoch同txn；重复通知不重复trial/reserve。SUSPEND核caller即阻新调用；RESUME必须同skill/lock的原评价仍适用，否→需新TRIAL；RETIRED终态，不复活。改body=新version重新评价。

## 5. load / execute

INSTRUCTIONS load只读取绑定skill的获准files/ranges，把来源加入E；相同文件/range/hash同request只一次。SkillUse的mode=INSTRUCTIONS及原call receipt表达读取，实际曝光由后续Provider输入receipt确认。重复load有真实轻量调用记录但不重复预算成模型执行。SCRIPT和WORKFLOW只由execute触发，不因load触发。

SCRIPT：原approved runner定义argv，不接受模型shell字符串；只允许整个token `{input_json}`/`{output_json}`由系统替换，固定cwd为原workspace，继承环境只使用原白名单，秘密用原临时凭据注入。输入先schema和max_bytes校验；output文件由系统命名；timeout停止原进程组，真正结束/UNKNOWN由原executor回执判断；exit0但缺文件/非法JSON/schema错=SKILL_OUTPUT_INVALID。stdout/stderr原artifact记录，超上限明确TRUNCATED且不能据缺省片段声明执行成功。

所有底层tool都重新过当前权限：caller/owner/TaskScope/Session/Skill/Tool交集。脚本没有默认网络或宿主文件系统访问；无法通过原sandbox落实该scope则不暴露该runner。外部效果依原OPS，不能用Python/浏览器名字宣称只读。

WORKFLOW：引用原冻结Workflow和原checkpoint协议；没有可用executor→WORKFLOW_UNAVAILABLE，不迁移到另一Runtime，不自行写Workflow调度器。
