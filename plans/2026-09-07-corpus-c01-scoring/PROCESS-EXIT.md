# 评分进程自然退出修复

2026-09-07，源码ab36b6a5 / 独立child新控制1PASS。C01-10首次真实结果保留：语义FAIL，单次物理Provider无toolcalls；SDK终态及公共trace完整，但资源180.249s deadline/exit125，PG67059清空。不会重跑同ID。PERSONA由主独立修改，本叶不改prompt/gold/原case。

## 确定来源与owner边界

原worker67061只读sample显示主线程在Py_FinalizeEx→wait_for_thread_shutdown；run()及asyncio退出已结束，cleanup_errors=[]不代表线程已退出。子67228为multiprocessing.resource_tracker，不能凭其存在认定模型子任务挂起。

实际bootstrap创建服务UoW并传execution_ports；WorkflowRunner仍独立创建自己的UoW，占用同workflow.db写入连接的另一个owner。原退出只关闭服务UoW，runner owner未释放。ExecutionWriteLane仅最后owner归零才关闭aiosqlite非daemon SimpleQueue worker。WeMM使用asyncio.to_thread；本次不能把残留直接归因embedder，也不删除模型/制品/data。

旧pytest全局conftest会关闭所有write lanes及跟踪的aiosqlite连接，因此旧main控制不是自然SystemExit证明；原结果范围保留，不把这项缺口算已验。

## 最小修改

- WorkflowRunner.close：只关闭自己独立创建的owner UoW，不关闭借用execution_ports。所有公开async工作入口计入正在执行的调用；关闭标记阻止新调用，等待原调用（含异常/取消的finally）退出后再释放UoW。嵌套原调用能继续完成，禁止从正在执行的同一调用内自等待close。重复close安全；等待阶段被取消时不关闭仍在使用的库，可再次收尾。
- WorkflowService.close：调用者先停止外部接入及借用者，随后原launcher.shutdown取消并join其工作；再等待runner.close，最后关闭服务自有UoW。提供的execution_ports默认借用，只有bootstrap显式owns_execution_uow=True转移该实际UoW的关闭责任。异常或取消导致未排空时不提前释放仍在使用的库；不会标记已关闭，可再次尝试。
- main lifespan与评分worker共同调用service.close；不调用test-only全局lane清理，不私改owner计数、不标daemon、不强退或用杀子当成功。未扩展其它workflow业务。

## 唯一新增控制

test_corpus_scoring_process_exit.py启动独立解释器scoring_exit_child.py。child无pytest导入/全局teardown，执行实际main factory/public setup/原SDK Run与Memory出站记录，然后正常SystemExit；仅内部网络Provider delegate固定单响应、socket网络禁用。使用控制专用USER，setup只借用既有明确映射，并非重新评估C01-10；无oracle/真实模型评分。

同时要求实际COMPLETED/公共Provider观测完整、cleanup_errors=[]、公共close重复调用安全；asyncio.run之后无非daemon Python worker，父进程实际得到exit0，资源入口最后确认整个PG清空。90秒child超时属于FAIL，外部cleanup不能替代自然退出通过。没有旧初始化/trace/审批绿重跑；本地Memory若按实际链加载WeMM如实记录。

资源顺序Hegel新控→Carver实际2call→本child1。该顺序已完成，结果如下。原r1现场位于primary `.local-test-evidence/2026-09-07/corpus-c01-real/{resource-r1,scoring-r1}`，不改写。

## 实际结果

exit-r1：1PASS17.01s，PG69388自然exit0，资源17.349s，峰1164368KiB，remaining=[]，cleanup=null，最低磁盘4423MiB。默认2GiB/180s/同共享锁；旧控制未重跑。child实际main完成一个本地固定Provider响应/公开terminal与完整Provider事实，正常SystemExit；不是网络模型评分。WeMM随实际Memory持久链加载，非无模型进程。

child在asyncio.run返回后线程清单仅MainThread非daemon；observability及两个tqdm_monitor均daemon。父进程收到child正常exit0后才通过，不能由父pytest的全局lane清理替代。重复public close通过；借用/并发/取消语义本轮按源码边界审阅，不声称分别跑过专门控制。

原始证据在本树 `.local-test-evidence/2026-09-07/corpus-c01-controls/exit-r1/`，不提交：

| 文件 | SHA-256 |
|---|---|
| command.log | 0a4e8763164bff4a981f9e042753891c0b3e1770ad5d771298db6bd445a98715 |
| resource.json | a9d0854f635244f6764af92ee2506cb016f027529cd26fa1b845253fd1e7ee9f |
| cases/test_actual_main_execution_exi0/child.log | 654c73f564f1cd9a0a9dad3488fbe018fc7db71db2995122af07c60aeb8ca49e |
| cases/test_actual_main_execution_exi0/lifecycle-control/thread-exit-inventory.json | f445add298618f3e7b3de1b4c9730827e046593b8ab9cc7421ef915ed01771c7 |

资源槽已交主，新C01-13沿主已修改PERSONA独立首次评分；C01-10原语义FAIL与退场deadline两事实均保留，不重试刷绿。本叶不修改主PERSONA、不在carrier给答案。
