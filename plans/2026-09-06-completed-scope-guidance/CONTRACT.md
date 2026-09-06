# r19 completed Scope 指导：必要实际链控制

2026-09-06。独立 `feat/completed-scope-guidance-controls` 从主修复 `a189ec4e`；notice `26c19b5e` 分支原样保留。生产源码仅主已修三入口closure措辞及effect gate的inactive/sticky公开指导，权限、生命周期、hash和预算不改。

主原两失败来自旧 `s5b_effect_gate_harness` 缺 `scope_disclosure_reader`，以及新test误取不存在的env.scope_store；未到达目标逻辑，不视为产品红，也不重写为绿。本叶不向旧fixture加allvisible/pass-through、不迁移整个旧测试架构。

两条新控复用当前真实Host queue/SDK runtime及ScopeDisclosureReader、真实S1/绑定和当前Memory policy：

1. 合法Host公开来源的已complete Scope，实际context_route resume应成功返回status complete及来源manifest；真实write_file activation后写入被 `effect_gate_task_scope_not_active` 拒绝，文件不存在。下一物理Provider请求携带对应拒绝及生命周期指导；再次使用同route得到sticky拒绝及对应指导。Scope保持complete，绝不把resume误作生命周期复活。
2. 原用户目标包含创建并回读验证；实际SDK只写文件后结束回答。由生产专用closure authority/main resolver发出的真实物理请求，携带完整原目标、真实write事件与证据引用、原staged answer，并暴露真实task_scope_update工具。确定性Provider提交有来源的“仍需回读”resume.update，落库非complete且目标未缩小；read_file并未执行。不仅检查文案关键词，也核真实文件/工具事件/物理请求承诺/持久Scope状态。确定性响应不声称模型能正确判断全部任务完成，原生r19/24轮质量仍未完成。

先源码和Dirac挑战，主notice构建/native期间不跑资源；之后仅这两条新控，H079/M618既有target借用，不新venv/SDK制品、不复跑旧绿。
