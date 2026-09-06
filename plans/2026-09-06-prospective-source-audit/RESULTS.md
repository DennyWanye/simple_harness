# Host接收提醒来源读取观察：限定交付

2026-09-06；base `c98b6a27`，branch `feat/prospective-source-observation`。
固定产品源码`0e983edc`（首次源码`2ef063e3`，契约`49b23f0e`）；
Dirac只读源码及本批结果限定ACCEPT，不是人类独立审查。

新增14项全部通过，5.17s；3个既有模块pytest assert-rewrite warning。
真实public Manager/SQLite admission→mutation→outbox→默认Host登记读，
重开保留原observation、grant replay不捏造重复读；拒绝3项、取消/读失败2项、
写失败2项、开始写中重复取消1项、绑定变异/缺观察4项、读失败/v2缺失1项。
start-before-SDK已落盘；不存查询/原始outbox ID/原始异常，不伪造Run。
写故障不重放SDK调用，settlement故障保留pending；原SDK异常/取消传播。

初审P2已修复：SDK未调用或缺少v2方法时，Host attempt保留，但不生成
`owner_component=memory_sdk` finding；已有两控实际查sidecar确认0findings。
没有新schema/权限表，未修改grant规则、main/composition或任何SDK制品。

可接接口见[CONTRACT](CONTRACT.md)。`PublicRegistrationAuthoritySource`默认
启用同目录`operation-audit.db`；主可传`operation_audit=ProspectiveSourceJournal(path)`。
成功绑定标`captured_bound`，SDK原`host_persistence_unverified`保持不变，Host
settlement另存；不是SDK持久receipt或授权。v2只有operation精确调度/不fallback，
后继union DTO/观察契约尚待接入，不称M616支持v2。强杀/不可写盘/未调用wrapper
仍是覆盖缺口；未标完整SDK/Host全操作coverage、scheduler、401或native通过。

## 本次载体与可复跑命令

复用原tiny解释器的通用依赖，通过ignored脚本显式加载既有H075 installed-target及
M616 artifact-616/installed，再加载本树Host源码；是限定安装载体消费，不是本树
独立完整安装/pin验收。没有新venv、重建、旧包成员比对或旧H075绿色复跑。

在本树根目录，以新的证据目录名复跑（原目录不可覆盖）：

```sh
.local-test-evidence/2026-09-06/typed-use-primary/venv074614/bin/python \
  /Users/denny/projects/simple_harness-primary-candidate/scripts/run_resource_bounded.py \
  --evidence-dir .local-test-evidence/2026-09-06/prospective-source-audit/tests-r2 -- \
  .local-test-evidence/2026-09-06/typed-use-primary/venv074614/bin/python -I \
  .local-test-evidence/2026-09-06/prospective-source-audit/run_tests.py \
  .local-test-evidence/2026-09-06/prospective-source-audit/basetemp-r2
```

默认共享OS锁、2GiB/180s、1GiB磁盘准入。实际PID/PGID49962 exit0，资源总时
6.229s、peak185600KiB、remaining_group_members=[]、cleanup_error=null；锁已释放。
没有Provider/native，测试线程/进程已清空。原始证据均ignored，前缀
`.local-test-evidence/2026-09-06/prospective-source-audit/`：

| 文件 | SHA256 |
| --- | --- |
| tests-r1/command.log | 65eb1886e56cb8871b88d39e10053c88356f1125ab446be6d3a6bb05ba696650 |
| tests-r1/resource.json | 570c39170a1f09714a5ab4a31148a03c754130e7a8c4a25534e929f1574ea6a2 |
| run_tests.py | 7a0355f2f8ab2c7e1029c3ca457a1deb1d929d9fc04651bbb10e6ad611baa1ee |

## 文件范围

- `backend/deskpet/operation_audit/prospective_sources.py`：复用sidecar的公开读取观察接收。
- `backend/deskpet/memory/prospective_registration_source.py`：唯一实际读取外层接入及可注入journal。
- `backend/tests/operation_audit/test_prospective_sources.py`：14个决定性控制。
- 本目录CONTRACT/RESULTS及ARCHITECTURE三份事实源：限定状态与边界。

旧`feat/typed-use-primary-runtime@fe9e3660`保留；本新分支未合主，未push/tag。
