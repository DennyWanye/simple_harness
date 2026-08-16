# Spike：Tool package 无副作用显式 catalog 与 Workflow detachment

> 日期：2026-08-16  
> 状态：PASS（架构可行性）；71个handler的机械绑定仍属于正式Slice B实现  
> 仓库改动：无；迁移package、descriptor manifest与SDK public patch均在`/tmp`

## 命令

```bash
PYTHONPATH=/tmp/simple-harness-public-spike.n0pL3N/sdk-patched/src:/tmp/product-workflow-detachment:/Users/denny/projects/simple_harness/backend \
backend/.venv/bin/python \
/tmp/product-workflow-detachment/run.py
```

本轮使用正常`/tmp/product-workflow-detachment/migrated_tools` package import，无fake
`deskpet.tools`/`sys.modules`注入。旧Workflow guard保持；主动旧import报
`DETACHMENT_GUARD:deskpet.workflows.contracts`，完成后`blocked_loaded=[]`。

## Inventory parity

- pre-cutover reachable unique registrations=79：base44 + code10 + context1 + memory-recall1 +
  orchestration6 + OS16 + deferred新增2；唯一跨family重复是`web_fetch`，去重后79。
- final canonical identities=77 SDK Tools + 2 required Workflow profiles。
- 唯一canonical mapping：direct bypass `deepresearch`→`workflow.deep_research`/
  `deep_research@v7-sdk1`；`ppt_pro`→`workflow.presentation`/`ppt_pro@v2`。`ppt_create`仍是Tool。
- Phase A从当前真实`deskpet.tools` boot registry并实际调用OS/Code/Context/Memory/Orchestration/
  Capability family builders导出79 unique；移除两个bypass后得到77个真实`ToolSpec`。真实manifest包含
  canonical schema、handler/context-handler identity、permission、build/effect/resource/outcome/dispatch/
  lifecycle metadata，SHA-256：
  `891ae13615229ee98715f8b18f39a5a045c1f995a29e984a4b86c4eaa2f310bf`。

77 Tool keys：

```text
agent, agent_parallel, agent_reach_doctor, agent_reach_read, app_discover,
app_launch, await_subagents, capability_build, capability_repair,
context_page_in, desktop_create_file, doc_create, doc_edit, doc_read,
download_file, edit_file, excel_create, external_action_wait,
fetch_tool_result, file_glob, file_grep, file_organize, file_read,
file_write, generate_image, glob, gold_price_lookup, grep, image_ocr,
list_directory, memory_forget, memory_read, memory_recall, memory_search,
memory_write, move_file, office_pick_file, pdf_export, ppt_create,
process_list, process_start, process_stop, process_wait,
project_directory_select, project_group_send, read_file,
register_artifacts, run_browser_task, run_shell, scrapling_fetch,
screen_capture, screen_click, screen_key, screen_move, screen_scroll,
screen_type, skill_invoke, spawn_subagents, spawn_team, todo_complete,
todo_write, tool_activate, tool_describe, tool_search, web_crawl,
web_extract_article, web_fetch, web_read_sitemap, web_search,
window_capture, window_focus, window_key, window_list, workflow_spawn,
workspace_prepare, workspace_recall, write_file
```

## Workflow runtime result

- DeepResearch：completed→close/reopen completed→recover completed，8 checkpoints。
- PPT：waiting→close/reopen waiting→public`resolve_and_resume(cancel)` cancelled→recover cancelled，
  14 checkpoints。
- close前typed physical calls：LLM4/search1/fetch1/artifact1；reopen后旧调用新增全0。

## 真实 SDK registration 与代表调用

- 77个真实schema+sidecar records全部通过SDK validator并注册为`FunctionTool`；sidecar保持Phase A真实
  hash。旧Workflow guard下`blocked_loaded=[]`，正常import包括`migrated_tools.os_tools`与
  `migrated_tools.orchestration_controls`。
- 六类真实产品handler已机械迁移并实际invoke：sync `file_read`读到`real-sync-handler`；async
  `process_list`返回真实进程项；context-bound `context_page_in`返回绑定内容；staged `write_file`实际写
  19 bytes；control `workflow_spawn`真实fail-closed；provider `web_search`经typed gateway double返回结果。
- 14个schema需要显式新version/hash：`app_launch/process_start`改typed bounded environment map；
  `capability_build`改bounded key/value_json array；`capability_repair/download_file/move_file`改exact 64
  length+handler validation；`doc_create/doc_edit/excel_create/ppt_create`的union改bounded JSON string；
  `window_capture/window_focus/window_key`改minimum 0+handler validation；`workflow_spawn.workspace_ref`
  改omitted-or-bounded-string。其余63项原样通过。
- 14项old/new canonical hash完整输出保存在临时真实schema migration report；正式实现必须把逐项hash
  固化进checked-in migration manifest。

架构spike不冒充正式绑定完成：除上述六类外的71项使用`not_exercised` fail-closed adapter，未声称真实
handler全部完成。它证明了真实metadata→SDK schema/sidecar→六类handler调用的关键依赖反转可行；
Slice B B3仍必须完成77/77真实factory identity绑定与全inventory tests才能出receipt。旧synthetic digest
`49b27c...`永久作废。
