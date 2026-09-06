# 原生 r25：真实 Procedure 查询为空

更新：2026-09-07。固定Host d86e4805（产品0e146792、UI2adf9088），H079/M619/S0313；新原生包 Verify 2adf9088p18120。沿用r24原userdata，无原始输入/数据库修补。本轮只发两条实际用户消息，两次工具权限均逐次展开参数后真实点击“允许一次”。

## 实际观察

冷启动已连接、主对话空闲，旧历史保留。第一条要求从记忆查找“松柏记录”待定流程草稿，不执行。模型调用task_scope_search，返回既有任务的active状态；最终误称其为流程草稿active。本项是来源/实体语义失败，不能算Procedure发现通过。

第二条明确区分任务状态与长期记忆候选Procedure，仍只查找。模型实际调用procedure_discover，query为“松柏记录”、after为空；SDK工具成功返回candidates=[]、next_after=null、omitted_oversize=0、execution_authorized=false。模型如实回复无匹配项，没有执行流程。由此仅证明真实发现工具可调用且空结果被诚实展示；正向草稿生成/发现/使用/三Scope晋升仍未通过。原r24记忆列表条目不能当作Procedure草稿存在证明。

Dirac核对r24、r25首请求的公开Provider projection并与日志request_ref逐一对应，实际12工具包含procedure_discover/procedure_use；Host/SDK序列化按原tools发送，排除“只在inventory存在、被profile/window裁掉”这一假说，不解释模型为何初次选错工具。

两次当前授权弹窗及正常final均可实际操作，未见原旧waiting残留；未重现Provider unknown场景，因此不声称全部异常UI验收通过。本轮不计240语料质量执行。

## 退出及资源

两条消息均结束空闲后Cmd+Q正常退出。PG62018 exit0，530.59s，峰1324592KiB，RSS限8192MiB/1800s，stop_reason=null，remaining[]，cleanupnull。非内存/超时拦截。

最低磁盘降至501MiB，未触256MiB运行终止，但低于下一轮1024MiB准入。退出后确认无cargo/rustc/simple-harness运行，仅清理本轮可再生target/debug/deps、build、incremental；两份原生app executable SHA前后相同，空间504→1681MiB。未删除原始证据、模型或userdata。独立caffeinate PID56392仍保持防熄屏，待整个测试结束才解除。

## ignored 原始证据

- `.local-test-evidence/2026-09-07/native079619/primary-ui-bc0iqg6l/00-restored.ax.txt` SHA256 `df7a9535d5d8b4bfaa7939c02b1d203595207dd6bc598b5e73b94dea659378df`
- `.local-test-evidence/2026-09-07/native079619/primary-ui-bc0iqg6l/00-restored.png` SHA256 `813e25713c7f70c1292bca11c2141d628134927f11367ef2ad324dd39c5429fe`
- `.local-test-evidence/2026-09-07/native079619/primary-ui-bc0iqg6l/01-task-search-permission.ax.txt` SHA256 `7bcb1f61fa1683c0bec1e2a279432995841201bb99318462f35774f9be5c1617`
- `.local-test-evidence/2026-09-07/native079619/primary-ui-bc0iqg6l/01-task-search-permission.png` SHA256 `b75a2b5e23e7bff451ef711aeccc754c7a799f9b3fcc6f4ef702074a369098c0`
- `.local-test-evidence/2026-09-07/native079619/primary-ui-bc0iqg6l/02-task-scope-misidentified.ax.txt` SHA256 `35eea92d60ec19c2b470db887c94c0a90df4208e26f812e14239405392023420`
- `.local-test-evidence/2026-09-07/native079619/primary-ui-bc0iqg6l/02-task-scope-misidentified.png` SHA256 `a00ba8abbec96e84ee1a134d967f69529a3727f7d12045629b7399c99b0de682`
- `.local-test-evidence/2026-09-07/native079619/primary-ui-bc0iqg6l/03-procedure-permission.ax.txt` SHA256 `d177a8a6b043782076a69e20b0f59a808b886a7ad93bd2aee0ebd37c61a7cdee`
- `.local-test-evidence/2026-09-07/native079619/primary-ui-bc0iqg6l/03-procedure-permission.png` SHA256 `3adacb66b669cd1db8f3285f7f29ae193ee59b525aec27fade0339795b1e6e91`
- `.local-test-evidence/2026-09-07/native079619/primary-ui-bc0iqg6l/04-procedure-empty.ax.txt` SHA256 `0d50179afe46de5e419e5e7b0beb8f421717d0265e033ac4808b247daa50ca65`
- `.local-test-evidence/2026-09-07/native079619/primary-ui-bc0iqg6l/04-procedure-empty.png` SHA256 `e2aa8f11feb72e83642a0eba25d1909d5ba1a176a3d1a271b396f212135c2e6c`
- `.local-test-evidence/2026-09-07/native079619/primary-ui-bc0iqg6l/launch.json` SHA256 `62fba66e4e5d9a903339fc500a12f4c8a38a5f61467d09fb3353e0f54cb8c35c`
- `.local-test-evidence/2026-09-07/native079619/primary-ui-bc0iqg6l/native.log` SHA256 `922c86847fd3b629bef8f74009994acbac8af26d647c11159e5d470ed8163abd`
- `.local-test-evidence/2026-09-07/native079619/r25-procedure/resource.json` SHA256 `7792fcfe0d3a0b50d0852948a1d292ae881a581a6178acdd6fd25fa30148332b`
- `.local-test-evidence/2026-09-07/native-decision-build/post-r25-cache-cleanup.json` SHA256 `89ba91be83c2091f2aa697a9564c51d90509085d9fdcc920148510fce00c94a6`
