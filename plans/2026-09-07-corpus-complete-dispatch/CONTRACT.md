# C01-06/11 正式 session 分派

2026-09-07。独立分支 feat/corpus-complete-dispatch，固定基线61f474e5；本批仅源码，NOT_RUN。主完整候选/实际vendor target统一验证；不在本稀疏树安装、改metadata、改guard或起资源进程。

## 已实现接线

唯一业务文件 `backend/deskpet/quality/corpus_scoring_session.py`：

- C01-06选择已审 `compile_c01_revision_setup`，严格只收原setup及可信时钟。先打开真实primary；`open_c01_revision_fixture`在同Host/Memory路径执行真实job创建B、公开action把同ID revision1改为revision2的A。沿原helper精确receipt/head检查，不复制旧节点成为两个新memory。
- 使用AsyncExitStack拥有fixture manager/authority，成功或失败均先退出关闭，之后实际main按原路径重开同一Memory DB、构造生产runtime。fixture action/delivery authority不注入生产，不增新的业务ledger。结果另存原CREATE及REVISE的plan/receipt/ref、实际analysis request/application/ingestion receipt，原source_pair/labels结构保持。
- C01-11使用原scalar compile/setup job。基线main已经把owned HumanMemoryV7Runtime.semantic_clock传给PrimaryForegroundContextPort，日期进入原system/budget/hash；解除旧blocked分支，不向USER拼日期、gold或类型。结果的clock字段只记CONFIGURED_MAIN_PRIMARY_SEMANTIC_CLOCK_NOT_OBSERVED，不把配置或seed成功冒称实际物理输入已观察。
- 当前输入、公开trace、approval、终态sink及资源finally维持原链。setup仍只读input/setup，不读取oracle。06的fixture/source不是评分对话；实际模型须自行选择/召回，helper通过不等于质量通过。

## 范围门与协作

`prepare_batch`仍仅支持既有C01/C07，不开放C08。C08主已13项限定准备通过，另8项真实派生source仍partial；不以本接线变为全20ready。C05 helper由Carver独占：当前固定9460edd8，04/09/14/20 archive/source及setup-prefix history有证据；完整session phase尚未固定，尤其16–19不ready。本批不接ignored runtime WIP、不改其文件。

## 待主统一组合

旧C01 revision/clock两控和C07 actualmain相位结果由主复用，不重跑。此dispatch尚未测试/独审通过；最小新增目标为C01-06通过正式session完成setup关闭→main重开→实际评分请求/公开trace，核同ID只rev2、source未混入对话、失败时无评分调用。C01-11若主认为需要session层额外覆盖，只核新分派实际请求的可信日期及原USER；不重跑旧clock矩阵。测试Oracle取实际public receipt/request及原输入，不以模型回答或错误消息镜像代替。

## C07 原失败延续

原分支phase-r1（缺tracked资产）与phase-r2（借用target的direct_url与本树vendor路径不符）均未进入相位请求，原raw保留。详细两批索引/SHA在旧分支文档commit `6f4e3cf224e254fc054241ef424072fe19754bca`，可独立取到主；b53仅文档，asset materialize没有资源文件commit。

主已给并经Dirac限定接受的当前组合：0aa8e029首批C01两控PASS、C07末尾错误oracle FAIL；61f474e5修正“尝试被拦”与“实际加载”的区别后仅C07 1PASS7.28s。相位已CONFIRMED、两Run/精确recent评分HTTP已验证；不是本树复测或真实模型质量，原diagnostics_snapshot RuntimeWarning仍保留。
