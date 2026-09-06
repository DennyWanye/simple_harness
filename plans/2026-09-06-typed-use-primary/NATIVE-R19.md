# 原生 r19：独立旅程前五轮及任务增补失败

更新：2026-09-06。固定Host86ef3eb952a5fbcd47d70d01575b365f4082f91c，H079/M618/S0313，新userdata，原生CUA/真实gpt-5.5。第一组计划24轮只推进5轮，完整旅程未完成。第4轮执行失败后经原生按钮停止，未改数据库或重发用户请求。

|轮|真实观察|结论|
|---|---|---|
|1|偏好声明“最多三条短列表”获得正常回复|仅前台响应，不冒称后台记忆验收|
|2|句子精简获得正常独立回复|此输入响应通过|
|3|create_new成功，真实doc_create生成说明docx，task_scope_update收尾，最终给路径；没有doc_read|创建部分通过；用户要求的文件核对未做，不计整步PASS|
|4|搜索原任务→resume_existing成功→doc_edit拒绝scope_not_active；continue_active成功后仍拒绝，后续两个doc_edit报route_receipt_rejected；再次请求路由时停止|增补FAIL；最后一次路由未允许/未执行，未称模型自行收敛|
|5|停止后独立“17加26”实际回答43，界面空闲|停止后可继续对话的限定通过|

真实TaskScope `fe989c33-131e-584c-83b4-dfbfa358ee05`，创建Host Run `653cc23a-35d8-5c82-8bcb-a3f24bb2c568`。创建closure实际将状态置complete（rev2），包含task.resume、goal.set、task.complete(doc_created)。只读源码和关闭后的Host库确认：resume路由不改变生命周期；effect gate仅允许active/open，拒绝complete符合现有规则，但统一错误提示要求重路由导致无效重试。原冻结§7禁止complete后恢复，本轮不能靠把门放宽或改原库追认成功；合法续做路径与提前完成语义继续修复。

外部只读docx实际正文为标题、负责人林舟、范围中文标题校对、“工作要求：检查文件内容。”，无新增清单。这是观察者在应用退出后的文件核验，不是模型执行了readback。Host只读库已关闭且无WAL，使用mode=ro&immutable=1/query_only读取；前两次普通只读连接报unable to open，原文件未修改。

截图/AX接口有瞬时错误：首capture失败后恢复；粘贴报timeout但AX已有原文，只按一次Return，未重复paste；后段scroll的noWindowsAvailable持续，AX读取、输入、截图和退出仍可用。04/05截图停留在旧工具历史，最终错误/43由保存的完整原生AX给出，不能声称截图展示了不可见末尾。未将工具/数据库证据当作UI替代。

正常Cmd+Q后PG29074 exit0，828.867秒，峰1,297,008KiB，remaining=[]、cleanup_error=null、最低磁盘2231MiB。未触发内存/时限/预算拦截；不是循环运行同一测试。两组完整旅程和240质量仍未完成。

## 本机 ignored 索引与SHA-256

- `.local-test-evidence/2026-09-06/native079618/primary-ui-spug44pt/00-empty.png`：`aed3e12c027e8b095cfe8b8150dc86f39f876e7f309beead5282d85180864695`
- `.local-test-evidence/2026-09-06/native079618/primary-ui-spug44pt/01-preference.ax.txt`：`f065e2730864adc5d896af23e3c83cff305aabf2a94833fea39045978883f1ce`
- `.local-test-evidence/2026-09-06/native079618/primary-ui-spug44pt/01-preference.png`：`57f4e72a41ba4c8055aa956386cea91bc56504d2cb4fa16573617c267f7857f9`
- `.local-test-evidence/2026-09-06/native079618/primary-ui-spug44pt/02-standalone.ax.txt`：`d7c56afd3016cc0642e4555e5b531bc755bba855828c368a3ddc404a0879a1d9`
- `.local-test-evidence/2026-09-06/native079618/primary-ui-spug44pt/02-standalone.png`：`3f646b821e8fe1ed3a2a6acea52e22cad293cea63005e99b2d9b73c952663c8f`
- `.local-test-evidence/2026-09-06/native079618/primary-ui-spug44pt/03-create-route.ax.txt`：`969c0c78e4471022e83db982bdd465c174fed66281c03b5cfd9f9b0fcd20e3f5`
- `.local-test-evidence/2026-09-06/native079618/primary-ui-spug44pt/03-create-route.png`：`b35995a26b166950b4f6a70f3a50812ed3c81fce042d2835143d45600fb74986`
- `.local-test-evidence/2026-09-06/native079618/primary-ui-spug44pt/03-created-no-readback.ax.txt`：`293aa46be23a8a11ccdfbfcc729033282d110b0f991fa10127ef65997a32d2b5`
- `.local-test-evidence/2026-09-06/native079618/primary-ui-spug44pt/03-created-no-readback.png`：`b6d5319314385a612961943d80fac029ba8d6bde33fa32d44148abf4b8b0152a`
- `.local-test-evidence/2026-09-06/native079618/primary-ui-spug44pt/03-task-update.ax.txt`：`415d390e99a11f3a78ff7655c46f484cfac38cf1cd9d067e6b190d07f5f0c08e`
- `.local-test-evidence/2026-09-06/native079618/primary-ui-spug44pt/03-task-update.png`：`57502ed5b448b0254828da449e2d4bc98a8ff901ca7d8f32cc2bf8e580696efe`
- `.local-test-evidence/2026-09-06/native079618/primary-ui-spug44pt/04-repeated-route.ax.txt`：`c8efe5428fda774b7929a8c668587d426c2eeb3730a8debb5e4d11c7bdc8a2e1`
- `.local-test-evidence/2026-09-06/native079618/primary-ui-spug44pt/04-repeated-route.png`：`f7dad5ff9fe6153b5ff7c476c88c660f26dee87e3565c3261c68c75e18364c26`
- `.local-test-evidence/2026-09-06/native079618/primary-ui-spug44pt/04-resume-route.ax.txt`：`abe8c0b6a8eff933ef25dd462b4dbc65b2a4adda7df102a172a73dab5d98698f`
- `.local-test-evidence/2026-09-06/native079618/primary-ui-spug44pt/04-resume-route.png`：`aa4805fb5a4d5032da3a625c020a80579c49472f3b49a10aa1fe23fad57ab2cc`
- `.local-test-evidence/2026-09-06/native079618/primary-ui-spug44pt/04-stopped-effect-rejected.ax.txt`：`a98273d869a6f073fa1600a1b9ad842fa787b555a7a7392c94437018934ffe79`
- `.local-test-evidence/2026-09-06/native079618/primary-ui-spug44pt/04-stopped-effect-rejected.png`：`a537030e4d068c6bef06738cc709dacfea0ca1cb59fe52f93dc758a30a8d0144`
- `.local-test-evidence/2026-09-06/native079618/primary-ui-spug44pt/05-after-stop.ax.txt`：`73bfb49dbf91ca949c719ecbc57252ccbf99ec50c0c02dcee84707ead2a1dc27`
- `.local-test-evidence/2026-09-06/native079618/primary-ui-spug44pt/05-after-stop.png`：`fccbb0bf25695577003d5280ca368081614ff5d942e83d1aa14cabe30fea5cf6`
- `.local-test-evidence/2026-09-06/native079618/primary-ui-spug44pt/document-readback.json`：`8d316dae6535367fecadc62cfbf04b5a3b09cb8f3d4ebe7bd70488ccec5b9bff`
- `.local-test-evidence/2026-09-06/native079618/primary-ui-spug44pt/launch.json`：`b90ab29fc6a08d7dd4fd22a3025587ce371b5306bf607de25c0a25b63bdcee5c`
- `.local-test-evidence/2026-09-06/native079618/primary-ui-spug44pt/native.log`：`0cfd795f369a6d37a00693958ee8d885b47a43a38752bcc5dee804479a56cd77`
- `.local-test-evidence/2026-09-06/native079618/primary-ui-spug44pt/scope-readback.json`：`a3bf071e4f2da5601adfef00eb627c0e81c2aa7bc34355b0a93aace4714de763`
- `.local-test-evidence/2026-09-06/native079618/r19-journey-a/resource.json`：`ad86de50868058bbdb47420b5998c9b6e30c6e953c0e84e6bd89702de8fac6be`
