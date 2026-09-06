# 已完成项目的合法续改：新 Scope + 原 root

2026-09-06。独立 `feat/completed-scope-new-workspace`，base `cb7ed574`（生产a189）。主原native与H079/M618不改；尚未测试，不算交付。

原义务是 Memory program S5b `design-freeze.md` §7：completed后禁止plan/status重开；route resume只是公开历史/status读取。现Host `HumanMemoryHostService.create_task_scope`、`append_binding`和`WorkspaceBindingRuntimeAuthority`已有真实新active Scope及AUTO配置/MANUAL持久挑战授权，`context_route.create_new`缺少选择原root的前台参数，固定新task目录导致不能编辑原文档。

## 最小公开调用

扩展已有 `context_route(route=create_new,title,goal,reuse_workspace_of,expected_source_hash)`。后两字段仅用于本分支：精确已完成旧Scope ID及公开search/resume返回的source_hash。无model bool、无任意path、无第六SDK路由枚举。未传reuse保持原新目录行为。

Host先由公开owned `open_task_scope`核精确source，正常ScopeDisclosureReader核当前披露，要求旧status complete/completed且binding hash/revision与当前公开receipt一致。只支持receipt恰好一个root；多root拒绝明确需选择能力，不猜/不合并。用现公开 `verify_effect_authority`核root membership、path+inode。只取真实root作为新binding目标，不复制旧goal/正文/权限。新的title/goal来自本Run提案及现producer dependencies。

随后通过真实service创建新active Scope，显式调用一次 `append_binding`，新scope/root/idempotency/evidence绑定真实Host Run。沿原AUTO配置授权及MANUAL独立持久challenge；context_route的原工具permission前置不变。模型不能提交allow或改变binding mode，MANUAL未决/deny绝无route写权限。默认AUTO在实际配置root内授权，不新造第二authority。

补一个可选Host `AppendBindingRequest.expected_filesystem_identity_hash`，由上述公开root DTO计算取得；写入这次新S1并传给既有binding authority。proposal捕获root时必须匹配，后续原SDK grant/store在commit核同canonical path+inode，防止异步期间原路径换成另目录后仍绑定。旧字段None时保持原payload/hash/port调用不变，无schema/SDK版本更改。新scope的root_id不同是合法新身份，不伪称旧root_identity_hash相同。

只有真实binding ACK后才提交新scope的CREATE_NEW route；后续文件effect仍走原active/binding/envelope/final guard。返回新scope/新binding及无路径的旧scope/source/binding引用，声明旧Scope未重开。无grant期间可有真实未绑定newScope，不能说完成续改；原跨库/UNKNOWN规则不变，不能重发unknown以凑成功。

## 必要新控制

1. 真实旧complete Scope+原文件；实际前台search/resume取source，create_new显式原root，真实tool activation/edit旧文件并下一物理请求读结果。新Scope active且root path/inode相同、root_id/receipt属于新Scope；旧head/事件/绑定字节不变。
2. 错source hash/foreign Scope/多root或错误selector拒绝，不创建可用新route、不写原文件。至少一次真实原栈拒绝传下一Provider，而非仅函数返回。
3. MANUAL真实challenge绑定newScope+原root，未授权无route，真实公开decide允许后原resume_existing(newScope)可用；deny不能由模型bool绕过。无需新UI authority。
4. root身份在检查后/proposal前变化拒绝零binding；旧source+新Run重复原action/effect遵守已有幂等/未知边界。只新增必要控制，不复跑刚绿指导2控。

限制：单root，非完整24轮模型质量/native；现已完成Scope的history/status只读，不将其read_file effect放行。实际原文件续改的权限来自新Scope新grant。

实现前校准：现产品 `_result` 对handler失败只保留code/message，manual challenge结构不会给模型；本叶不把它伪称已实现native绑定弹窗。MANUAL正控通过真实公开authority返回值交真实service.decide的人类控制侧调用，模型看到的仍是拒绝，之后公开resume新Scope。默认AUTO实际Run/config/grant路径是本次生产闭环；MANUAL原生交互发现/呈现尚不在本叶完成声明。

## 首批真实约束校准（2026-09-07）

原r1为1PASS/5FAIL，inode pin拒绝绿保留。正向先resume旧Scope再create新Scope，两个route曾落地而后续effect被原Run单Scope证据绑定拒绝；这是真实不支持的同Run换Scope，不抹红。三个负控已到真实拒绝，后面错误要求resume新产生的合法Host事件也不得追加，oracle错误另记。

已授权可交付路径固定为新Run **公开task_scope_search返回complete/source/continuation提案→create_new显式原root为本Run首个Scope→真实文件effect**。测试从实际HTTP中的公开搜索返回值取Scope/hash，不在Provider fixture偷注expected pin。搜索候选/工具说明不再强制先resume已complete Scope。

已绑定旧Scope的Run，在创建前核实际Tool envelope及本Run所有task route（standalone不能隐藏），明确返回下一Run先search的指导；不造新Scope/新grant/第二acceptedroute。在发布新route的同BEGIN IMMEDIATE内再次通过原evidence ingress公有resolve_run_scope_tx及已持久task route核未绑定，防止读后状态变化。原单Scope账本不改、不回填原r1失败。新增原双Scope路径负控要求实际下一物理请求见明确拒绝，旧canonical/binding不变、旧事件前缀不变，合法新增tool/terminal事件逐一核类型及本Run归属。
