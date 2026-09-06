# C05-18：真实候选之后的 revision 相位

2026-09-07，源码 **NOT_RUN**。后继从6bef，经独立07入口修复0915与C09接线2199/778继续；这两项主组合另测，本叶不重复。未改SDK/fixture定义/阈值/原有证据。

## 实际生产端口

首 setup 和评分 preview 仍原 actualmain。只有原 compiler 的 C18 `append_predefined_current_revision` 在真实非空候选、完成终态且无正式任务绑定后触发。空预览不修订、不发f1；不根据模型所选ID/gold临时选 mutation目标。

`TaskRevisionPhase` 取 history reader 已完整验证的原单一A archive；公共当前open核原source hash，原 scoring Run 必须 completed且binding为原评分Provider。然后：

1. 新 process-only `corpus-task-revision-fixture`（priority0）绑定新Run，旧setup priority2/评分priority1条目均保留。禁止在 resolver 有active绑定时切阶段；session_db经现公开reconcile记录新catalog。
2. 原 `prepare_scope_archive` 真实 `resume_existing` 带原exact source hash→实际marker写→真实closure instruction→`resume.update=待确认图片`。新增审批只认C18已验原archive、原terminal/route、当前官方open、真实pending/Provider调用及原root binding；不泛化原setup create许可。
3. 原source hash变化、canonical revision增加、当前resume精确新值、原两个SDK terminal不变、fixture实际public trace/hash/handoff完整后，才将该新setup turn加入history过滤。只过滤两个真实setup IDs，**保留夹在中间的评分preview**。
4. close/join本地fixture HTTP，新增 process-only `corpus-real-provider-after-task-revision`（priority-1）使用原评分endpoint/model/key，供下一独立Run。旧身份/配置不改，不向原绑定Run换服务器。新身份明确记为评分阶段；fixture Run只列action证据，不混评分HTTP/trace数量。
5. 原f1照脚本发送。现公开审批/route expected_source_hash和current disclosure保留；旧预览hash不能冒当前。真实评分模型可重新search；runner不把新source/最新值补进USER或SYSTEM。元数据中性合成标题维持原helper事实，不从“月报整理”反推设置标题；自然模型质量仍未验证。

独立phase目录与followup-action文件保留证据；写文件使用原exclusive模式，不覆盖原followup。失败/取消关闭自己HTTP，原mutation/Run若已持久不回滚、不伪造结果；当前载体单次attempt，未知/部分失败停止且保留，不声称具备自动重启恢复。

## 主统一执行的两个新增节点

`backend/tests/quality/test_corpus_c05_revision_phase.py`：

- `test_actual_revision_between_preview_and_selection`：原公开候选next=校对→真实中途fixture Run→原f1物理请求仍有先前评分对话、没有setup USER；以真实旧hash尝试resume，必须精确 `task_scope_source_stale`，随后真正重查得到新hash/next→真实resume→下一物理HTTP。两scoring Run与两个setup Run身份分离；旧terminal/source证据保留，评分HTTP只6次。
- `test_actual_empty_preview_does_not_start_revision_phase`：有真实非空设置库但使用无匹配查询，0 revision Provider/0 revision目录/0f1，仅初轮2评分HTTP。

以上是公开生产行为的受控HTTP组合，不是模型质量；源码未跑，不复跑07/08/旧四格/原空预览。

## 17/19首root当前准确限制

`context_route.py::_create_new` 默认唯一首root是 `configured_root/task-{actualscope}`；`reuse_workspace_of` 依赖既存旧scope且用于旧workspace续建。两者都不能凭现字段生成原spec的展板-甲/乙、workspace一/二。`HumanMemoryService.append_binding(AppendBindingRequest(root=...))` 本身能走真实授权，但在已有默认root之后调用会变第二binding，不能兑现first named root；直接service建scope也没有现ordinary disclosure要求的真实context_tool CREATE生产S1链。

因此17/19保持未开放。可行的最小后继位于 **Host context_route schema/handler 的明确首子目录选择**，仍调用现create/append binding/current receipt/Manual或Auto授权及exact inode验证，不写SDK SQL、不更名原root、不授父目录。需要同时固定source S1/提议、basename验证、重试同idempotency和双named-root真实控制；本18 commit不混此公开tool扩展，也不通过挑UUID/创建顺序绕过排名。
