# Procedure 恢复新控结果

最终独审补充（2026-09-06）：Dirac已限定ACCEPT固定Host a7a9ca66，结合此前ea63ddc6/c76da29c与Memory978ae99的12项，本恢复叶13个唯一source-overlay控制限定接受，旧12未重跑。新1PASS、5份raw SHA、2固定源hash和13tracked WIP恢复已独立只读核对。不是新制品/installed/native/TC-HM04全通过；UNBOUND发现和失败归因WIP不在结论内。下文“待终审”为此前记录。

## 2026-09-06 Dirac 恢复重复Scope P1修订

Dirac核过原12项及6份证据，指出已有prepared的恢复分支漏接SDK公开`MemoryValidationError(procedure_observation_source_already_counted)`；此前整片没有最终ACCEPT。源码 `a7a9ca66` 共用初次/恢复的exact拒绝收口，保留A原prepared/ref和B真实result，不签替代authority、不伪consume。只有该错误持久rejected，其他异常继续传播。

新增唯一真实序列控制 `test_procedure_duplicate_recovery.py`：A真实完整Scope使用后持久prepared未consume；B同Scope另一真实Run先consume成功数1；A原ref stale后重基遭duplicate，落Host rejected；B计数/结果及A原ref不变、无新attempt/消费；A下次visit零SDK调用。**1 PASS / 7.24s**，不重跑原12项。

固定测试源码：Host `a7a9ca66a7be2f0e874998fb45a5559ef43067e5`，SDK `da7728b2a2836e5b39e9bbde40e67fb1838df457`（业务978ae99）。主native结束后以默认OS锁 `--rss-mib 2048 --seconds 180` 执行相同现有Python；载体 `run_recovery_p1_fixed.py` 先将13个tracked草稿WIP文件保存到本批ignored副本，再恢复fixed HEAD测试，finally逐字节还原，全部true。SIGTERM也走恢复；未导入untracked草稿模块，未创建新树/venv，vendor恢复。

PG34197 exit0／8.117s／峰259968KiB／remaining=[]／cleanup=null，默认锁释放，已通知主/Hegel。该结果仅新恢复P1，非模型/native；草稿发现/失败前缀WIP未验证、未混入。待Dirac对此修订终审，不将原12控制追认成此前无P1。

- `.local-test-evidence/2026-09-06/procedure-scope/recovery-p1-r1/command.log` SHA256 `882ad70b42c905680df071b60593724f17ef6c3b8e4dfd0b527b89c32214f01a`
- `.local-test-evidence/2026-09-06/procedure-scope/recovery-p1-r1/resource.json` SHA256 `3fbbe287e0782b3eba16b1b82162024c7d6e51265b9d6f0e5ad773a5fcd4f701`
- `.local-test-evidence/2026-09-06/procedure-scope/recovery-p1-r1/source-state.json` SHA256 `65d629cdd9ed1df4217bc4682897eb54608c8169e71350afc2ea65b172a7a86b`
- `.local-test-evidence/2026-09-06/procedure-scope/recovery-p1-r1/wip-preservation.json` SHA256 `2ec410a69bc5470eba72a42c2d180cfb08f759b42f41db0d4ea4a6061fb48ed0`
- `.local-test-evidence/2026-09-06/procedure-scope/recovery-p1-r1/wip-restored.json` SHA256 `ddb8abf7404ebfb5ff3caff0c567feb1da2ca556771fce4253e228944da1c203`
- carrier `run_recovery_p1_fixed.py` SHA256 `e274c303577277d126c7037d4e1c528652ffd7bff87089ad4bbb0fc7342ef29c`
- carrier `run_recovery.py` SHA256 `45bcf32002044d7575b6cb8aad858440cab44c82ba39fdb247e3e3819e0e36d5`
- fixed `a7a9ca66:backend/deskpet/memory/procedure_runtime.py` SHA256 `a6ddf846d5c3f8c749c1a1ac7b7e44242ae8ea1570277faed0add460b0b347c1`
- fixed `a7a9ca66:backend/tests/memory/test_procedure_duplicate_recovery.py` SHA256 `e6bd704b8e6af829c63475b717cdcb4f970f6bcb85f1945cac82355a374dfc36`


最后更新：2026-09-06。Host业务 `ea63ddc6`、夹具修复 `c76da29c`；Memory业务及控制 `978ae99`。各基线分别5e513eda/db7ca22。仅此新增叶12个唯一控制分批通过，不重跑之前三Scope/SDK绿，不是installed后继、真实模型/native或TC-HM04全通过。

## 结果和原失败

| 批次 | 结果 | 范围 |
| --- | --- | --- |
| recovery-r1 SDK | 3 PASS / 0.53s | 真实observation revision链/真实REVISE拒绝；旧ref仍有效拒续、过期续期、已消费禁止新签、换operation拒绝；prepare后forget不能由旧未消费authority覆盖。 |
| recovery-r1 Host | 5 PASS、4 FAIL / 37.31s | 54回滚/旧registry/fence、真实prepared timer、未消费过期重开、已消费lostACK原ref、两个实际独立Scope绑定同旧revision后顺序消费通过。四失败：同Scope再次运行、tool/workspace漂移、highrisk三Scope退出。 |
| recovery-r2 Host | 4 PASS / 20.50s | 只重跑上述四失败。同Scope第二Run真实prepare拒绝并持久rejected无新authority/计数；真实绑定后变更工具身份/目录inode，均零step reservation/effect/文件；highrisk三个实际Scope成功数1/2/3但一直DRAFT。 |

原失败中，新夹具直接 `_drive_once` 与恢复唤醒出的 `_run_driver` 重叠：第二Run出现 `foreground_run_already_terminal`，漂移 `_drive_once=False`，高risk已执行三个观察但退出CancelledError。c76da29c仅新夹具改公共 `after_enqueue→drain` 使用唯一helper，并断言实际drift已发生；无产品门放松、不吞取消、不更改installed SDK。原r1仍保留，不把失败阶段追认为绿。

## 公共路径与证据边界

SDK严格默认revision不变，显式rebase要求同定义/qualification epoch且逐条真实consumption/result；旧authority只是来源，未消费/失效由SDK同事务判定。Host先试原ref，新增54只追加恢复尝试、旧53prepared及ref不改；lostACK原result无新attempt，无Provider重发。工具执行仍在原实际workspace/effect fence内，highrisk不授auto许可。默认main需要恢复能力版本1后显式升级54；M618旧native环境不动，待主与Hegel统一源码/制品。

测试用现有M0614 Python、H078/M618/S0313 target，并显式加入Memory及Host源码路径；public Memory操作和真实Host文件effects参与，模型选择为确定性测试Provider，没有LLM/native。Host verifier所需两个vendor文件仅测试载体内临时指向主候选H078，finally已git restore，未入commit。该覆盖环境不代表新wheel安装验证。

12个新运行侧库只读审计回读：以下仅Procedure operation，含原失败批发生的真实调用，不作为额外测试数量。所有列出的成功/拒绝均captured_bound，新参数/ref纳完整request绑定；同批timer既有prospective source另有一条absent，不声称全栈审计完整。

| operation | state | observation | 条数 |
| --- | --- | --- | ---: |
| prepare_procedure_observation | raised | captured_bound | 1 |
| prepare_procedure_observation | returned | captured_bound | 13 |
| read_procedure_use_target | returned | captured_bound | 64 |
| record_procedure_observation | raised | captured_bound | 1 |
| record_procedure_observation | returned | captured_bound | 13 |

## 命令与本地索引

Python：`/Users/denny/projects/simple_harness-primary-candidate/.local-test-evidence/2026-09-06/primary-m0614/venv/bin/python`。
资源入口：`/Users/denny/projects/simple_harness-test-resource-cleanup/scripts/run_resource_bounded.py`；两批均默认共享锁，`--rss-mib 2048 --seconds 180 -- <python> -B <carrier> <evidence-dir>`，未指定其他lock。
carrier：`.local-test-evidence/2026-09-06/procedure-scope/run_recovery.py`；r1不加选择参数，依次SDK test_procedure_recovery.py、Host test_procedure_recovery_schema.py和test_procedure_recovery_runtime.py；r2只选host及后三个函数（drift参数化两例）。资源receipt保留实际命令输出；source-state为实际固定commit。所有raw均ignored。

recovery-r1：PG20720 exit1／38.506s／峰271824KiB／remaining=[]／cleanup=None，锁已释放。

- `.local-test-evidence/2026-09-06/procedure-scope/recovery-r1/command.log` SHA256 `16f94ea4561709fc85e7f01535b5d175912b88ad4152ffe18221c8d7cea14419`
- `.local-test-evidence/2026-09-06/procedure-scope/recovery-r1/resource.json` SHA256 `7483e3e87aa0f8d15f71721350ca65814de0227fd0a47e12bbfa6ba508c8a75a`
- `.local-test-evidence/2026-09-06/procedure-scope/recovery-r1/source-state.json` SHA256 `e95de31098eae811b721a499fc2606c95ea96587ffed593d809bc4bb7dfad0d6`

recovery-r2：PG21115 exit0／21.085s／峰241920KiB／remaining=[]／cleanup=None，锁已释放。

- `.local-test-evidence/2026-09-06/procedure-scope/recovery-r2/command.log` SHA256 `e67eb7918b0bb160ada2ad0f894dcedf2df908ba2f064f91b349dd08f9b205fb`
- `.local-test-evidence/2026-09-06/procedure-scope/recovery-r2/resource.json` SHA256 `43486f245a297fc1d3a3e7b9458ed91867a49a69334db0d05189238fe96c4936`
- `.local-test-evidence/2026-09-06/procedure-scope/recovery-r2/source-state.json` SHA256 `43791462afb3b1524a0fb5c5a9a33290054f4fdb8545768a143d52bf4a273b8a`
- `.local-test-evidence/2026-09-06/procedure-scope/recovery-r2/audit-readback.json` SHA256 `a7e28b6905f931173fc9bcb52874e4e92fe7dfdf1930740a448b69decdb58b66`
- `.local-test-evidence/2026-09-06/procedure-scope/recovery-r2/source-members.json` SHA256 `9b697bd43ee1bbec5a6214b2a893cc33b8fe5fee3e4847c276a1c1f53c942b3b`

- carrier SHA256 `45bcf32002044d7575b6cb8aad858440cab44c82ba39fdb247e3e3819e0e36d5`

source-members只扫描本叶从5e513eda/db7ca22到c76da29c/978ae99的变更，记录结果文档更新前源码快照，不重扫旧包或旧raw。实现契约见[RECOVERY.md](RECOVERY.md)。请主转Dirac对新delta及原失败/续期链挑战；尚未获得本恢复叶独审接受。

仍未完成首次UNBOUND草稿产品发现、自动失败归因及真实模型/native全链；F01明确延期。9项Host包含真正产品接线，不能以此把Procedure recall等同实际激活或宣布整个TC-HM04完成。未独立分配SDK版本/构建，Hegel接口与本叶由主统一整合。
