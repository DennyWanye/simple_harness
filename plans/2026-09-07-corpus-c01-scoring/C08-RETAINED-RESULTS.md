# C08 保留历史摘要：实际 main 首批结果

2026-09-07，业务ca2fd31d（c4b94abd），测试fa9e7798（37c16d2e），同候选包括Host异步诊断d60a94f4。当前installed H0710/M619/S0313，不改SDK制品。

**5 PASS，38.78s，首次全绿**。四正常case C08-01/06/11/18分别使用独立新子进程及隔离数据库，另一个wrong-assistant负控。每项只读原setup及原current，校验mapping；child禁读gold和.env。原13项scalar绿未重跑。

正常每例先经实际main/原Provider HTTP adapter、SDK Run/终态、Host outbox保存原USER与一条明确的准备期assistant摘要/介绍。fixture loopback仅一个响应，完整phase trace及真实绑定验证后关闭；关闭原owned cognitive manager，临时同DB公开builder沿原USER作业lineage执行analysis，必须APPLIED+ACCEPTED、A非空且派生自该USER，随后公开EVIDENCE suppression使USER与assistant都history_suppressed、A从可见graph消失。原S1/登记引用保持不变、job随后IDLE。再关闭fixture并用原分析authority重开生产manager，检查冷重开可见性与原source，最后实际新Run的物理HTTP请求仅含原current USER，不含旧事实/摘要/setup。

每正常例fixture1次+受控评分HTTP1次，setup/current的Run及Provider invocation均不同；没有真实模型调用。wrong-assistant经实际loopback与SDK COMPLETED持久化了错误摘要，公开group精确校验拒绝，seed未启动、评分0次。不是直接往DB插错误终态。

边界：证明这四类真实保留assistant摘要/介绍的来源、抑制及出站隔离；不证明独立rolling-summary artifact、short generation、真实模型质量、原生UI或全部C08已准备。正式共享评分session尚未接入这些retained helper。此5控不增加240条质量PASS数。

资源：PG84053 exit0/39.477s/peak564336KiB/minDisk4465MiB，remaining=[]、cleanup=null、stop=null；五个子进程均自然退出，无pytest子进程清理替代。正常例各2次optional embedding尝试被本地控制拦截并降级，负例1次，实际模型加载均0。防熄屏保持至全测试结束。

顺带确认本批5个实际main初始化诊断文件均有memory+harness来源、health=ok/degraded_codes=[]，child.log无never-awaited警告，说明d60当前main接线已被本批新场景真实执行。Memory来源sdk_version仍保留其原错误值0.6.0，实际installed=0.6.19；该SDK字段修正另待，不能称元数据问题已解决。

命令：当前installed target优先PYTHONPATH与原primary-m0615 Python，经共享run_resource_bounded.py --rss-mib 2048 --seconds 180，pytest -q -x backend/tests/quality/test_corpus_c08_retained_main.py，basetemp在本批ignored目录。-x确保共因首红即停，本次没有触发。

| 本机原始证据 | SHA-256 |
|---|---|
| `.local-test-evidence/2026-09-07/corpus-c08-retained-main/r1/command.log` | `f192f850ad34966a4b3b673963a213cfa847be98a3e29646fd44e04316ea7482` |
| `.local-test-evidence/2026-09-07/corpus-c08-retained-main/r1/resource.json` | `d37b05f4de0b3c53fdc61a83e991e6234b13acc888aad3c0777be6f2d75a8cb0` |
| `.local-test-evidence/2026-09-07/corpus-c08-retained-main/r1/tmp/test_actual_main_retained_sour0/main-retained/control.json` | `41cc67434e5c8f543cd3a99974f3c66ce3f52669d92526b7fd88a698cf1f92ff` |
| `.local-test-evidence/2026-09-07/corpus-c08-retained-main/r1/tmp/test_actual_main_retained_sour0/main-retained/result.json` | `3628afa97ab47e97fd3763fc842a2ed7a606e86c84f1695f68b6cca4641a156c` |
| `.local-test-evidence/2026-09-07/corpus-c08-retained-main/r1/tmp/test_actual_main_retained_sour0/main-retained/child.log` | `13ffec3b95524c0dd5162377ab15f1ab948048cc912dd92249b4bb939b5b495e` |
| `.local-test-evidence/2026-09-07/corpus-c08-retained-main/r1/tmp/test_actual_main_retained_sour0/main-retained/runtime/logs/sdk-observability-snapshot.json` | `3f8d34b95d06742754b022bff52c62dc0ef5068b446eb9ac21c709e720203048` |
| `.local-test-evidence/2026-09-07/corpus-c08-retained-main/r1/tmp/test_actual_main_retained_sour1/main-retained/control.json` | `9b28950ccc6bc3942d55be0bdac2f3737fb422353f9dedc457d7486febc1304c` |
| `.local-test-evidence/2026-09-07/corpus-c08-retained-main/r1/tmp/test_actual_main_retained_sour1/main-retained/result.json` | `61528e908650aa5077bcb2dff0e27735af5d4b60f9cbf6c61a16423318a6e0af` |
| `.local-test-evidence/2026-09-07/corpus-c08-retained-main/r1/tmp/test_actual_main_retained_sour1/main-retained/child.log` | `cafb5263cb083a6a26f06b86b70e74e0d5d49fdac855dc5a50de2e70bcfc9b49` |
| `.local-test-evidence/2026-09-07/corpus-c08-retained-main/r1/tmp/test_actual_main_retained_sour1/main-retained/runtime/logs/sdk-observability-snapshot.json` | `3f8d34b95d06742754b022bff52c62dc0ef5068b446eb9ac21c709e720203048` |
| `.local-test-evidence/2026-09-07/corpus-c08-retained-main/r1/tmp/test_actual_main_retained_sour2/main-retained/control.json` | `ed6c29328ab4a493cd317ecc7212770bb53150d1a8a7ee37f0a1ca9018a67691` |
| `.local-test-evidence/2026-09-07/corpus-c08-retained-main/r1/tmp/test_actual_main_retained_sour2/main-retained/result.json` | `2eb98a9b4771e61ef4c7aeba125081e599388785ec80943c14bd35e4889d0ead` |
| `.local-test-evidence/2026-09-07/corpus-c08-retained-main/r1/tmp/test_actual_main_retained_sour2/main-retained/child.log` | `b091ce1bb26727e93d0a0b181a4a8c7e1d732dc0a6433dcc096e6230bf89e20f` |
| `.local-test-evidence/2026-09-07/corpus-c08-retained-main/r1/tmp/test_actual_main_retained_sour2/main-retained/runtime/logs/sdk-observability-snapshot.json` | `3f8d34b95d06742754b022bff52c62dc0ef5068b446eb9ac21c709e720203048` |
| `.local-test-evidence/2026-09-07/corpus-c08-retained-main/r1/tmp/test_actual_main_retained_sour3/main-retained/control.json` | `3d1368089b47d0cf9725bfabcad939c4cc0b0c56de8b33843154aae800802d07` |
| `.local-test-evidence/2026-09-07/corpus-c08-retained-main/r1/tmp/test_actual_main_retained_sour3/main-retained/result.json` | `c285dae38fa7ed14a367b7c86c23568c76ac04d606a5d1d934348a69151b9668` |
| `.local-test-evidence/2026-09-07/corpus-c08-retained-main/r1/tmp/test_actual_main_retained_sour3/main-retained/child.log` | `ea8961170e92665fe0cf427a2203d31637596d3d63c00f78d0d90263e6d27c48` |
| `.local-test-evidence/2026-09-07/corpus-c08-retained-main/r1/tmp/test_actual_main_retained_sour3/main-retained/runtime/logs/sdk-observability-snapshot.json` | `3f8d34b95d06742754b022bff52c62dc0ef5068b446eb9ac21c709e720203048` |
| `.local-test-evidence/2026-09-07/corpus-c08-retained-main/r1/tmp/test_actual_main_retained_sour4/main-retained/control.json` | `2b9e2c105d63148ff0c43aa6a26b2ab41c499bd9dca1eaa450b0078a7560d007` |
| `.local-test-evidence/2026-09-07/corpus-c08-retained-main/r1/tmp/test_actual_main_retained_sour4/main-retained/result.json` | `f26fd358c237a213320e9d9856cc7e3e97fa35b652ff14c6e7cccb8bd276d1fe` |
| `.local-test-evidence/2026-09-07/corpus-c08-retained-main/r1/tmp/test_actual_main_retained_sour4/main-retained/child.log` | `81f5517025398c36e66bcd28afe5636dd9dbaf14df0f4476589eb88336cac42e` |
| `.local-test-evidence/2026-09-07/corpus-c08-retained-main/r1/tmp/test_actual_main_retained_sour4/main-retained/runtime/logs/sdk-observability-snapshot.json` | `3f8d34b95d06742754b022bff52c62dc0ef5068b446eb9ac21c709e720203048` |
