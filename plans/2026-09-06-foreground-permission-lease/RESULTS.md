# Foreground permission lease 交付

最后更新：2026-09-06。独立 `feat/foreground-permission-lease`，base `8b7c05cb`。
产品 `9d48465f` + `e3345565`；测试最终 `68660080`。原guard分支d524与graph诊断分支保留。

## 结果与范围

Dirac对e334产品/d7ca测试最终限定ACCEPT。默认TTL仍300秒，测试独立设置1.2秒。
Runtime keeper跨授权WAITING续租，幂等键不复用；失租停止本owner工作；失败/取消清理
不依赖再读DB，成功后的最终读失败也先join。相同owner真实过期经原官方store回收gen+1。
恢复核原SDK Run/session/request、Hostclaimed/startintent/binding，不重新生成旧context/start；
未来Provider/tool鉴权保持原门。Stop继续投递，actual SDK FAILED仍结算FAILED，
actual cancelled与Stop intent才结算STOPPED，没有伪终态/改旧receipt/改SDK制品。

新10 unique分批各绿；原3业务绿色未重跑。包括两次真实授权WAITING跨已签发expiry后允许、
实际工具文件落地、live/expired WAITING Stop、真实SDKFAILED后过期回收Stop、
另一owner回收后旧heartbeat/旧权限响应拒绝、query unavailable/foreignidentity拒绝、
工作异常/取消/最终snapshot失败后的3项keeper清理。
终态断言实际读取非空Host receipt并核公开SDK terminal event/hash，另含错event/hash拒绝。

这些是同Runtime、同SDK stack的lease恢复；停keeper不是重建进程或重开SDK stack。
使用真实installed SDK/Host数据库/生产权限适配器，Provider是确定性fixture，无模型或native。
主报告r6已有真实长期物化、独立遗忘成功，但本leaf不将它们算lease修复或图谱PASS。
原r6失败原件未动；主合入候选后的真实native恢复仍待验证。

## 各批实际记录

| 批次 | pytest | 边界/处理 | PG / exit |
|---|---|---|---|
| r1 | 3 PASS、1 FAIL，8.33s | expired_failed前提过早读取到queued，尚未到失败终态验收 | 69072 / 1 |
| r2 | 1 FAIL、3 deselected，1.19s | 让真实continuation调度后，普通Provider异常正确成为UNKNOWN/WAITING；不得把它当FAILED | 69150 / 1 |
| r3 | 1 FAIL、3 deselected，1.20s | 公开明确拒绝异常误用位置参数，构造错误仍成为UNKNOWN；修关键字public_message | 69249 / 1 |
| r4 | 7 PASS、3 deselected，8.55s | 仅失败项及此前未执行项，产品e334未变 | 69294 / 0 |
| neighbor-r1 | 1 PASS、15 deselected，0.04s | 仅受query三ID检查影响的旧mock恢复用例；68660080补字段及准确WAITING通知断言 | 69449 / 0 |

不把邻居及重验相加成独立质量评分。全部remaining_group_members=[]、cleanup_error=null。
r4资源9.183s、峰174608KiB、minDisk2836MiB；最后邻居资源0.438s、峰57728KiB、
minDisk2832MiB。共享锁已释放，未重跑analysis14、旧全套或制品身份扫描。

## 可复跑命令

cwd为本隔离树。复用原tiny Python，脚本显式加载既有H075/M616 installed target与本树backend；
不宣称本树新独立安装/pin验证。NEW取未占用目录，不覆盖原证据。

```sh
PY=.local-test-evidence/2026-09-06/typed-use-primary/venv074614/bin/python
ROOT=.local-test-evidence/2026-09-06/foreground-permission-lease
"$PY" /Users/denny/projects/simple_harness-primary-candidate/scripts/run_resource_bounded.py \
  --evidence-dir "$ROOT/tests-NEW" -- "$PY" -I "$ROOT/run_tests.py" "$ROOT/basetemp-NEW"
```

r2/r3/r4另带 `-k 'not multiple_real_waiting and not live_waiting and not expired_waiting'`。
邻居改脚本为`run_neighbor.py`并带`-k restart_with_durable_binding`。保持默认共享锁2GiB/180s
及磁盘准入/停止阈值；BUSY75只报告、不循环。无需为交付重复上述绿色。

证据根 `.local-test-evidence/2026-09-06/foreground-permission-lease/`，raw全部ignored。

| 文件 | SHA-256 |
|---|---|
| tests-r1/command.log | 1d1235dc3ad32e431877af61e28f066fe87e38e5b87e046a15cd46258446a4ea |
| tests-r1/resource.json | 6878bedad4fc0fc82b8e43d782e8b3bce39881a77d15e245646aee6c1bb44a7e |
| tests-r2/command.log | 4d918f6651f344448df0ce0f13812e0117350328c6c5beec391102983995bc45 |
| tests-r2/resource.json | 67f7a421b2257197e17ed6f8ee8854096e72b1ea78aeba2c7bedbc3bbb55e21e |
| tests-r3/command.log | 2828c13697274ed5bc09c746bbefb45f2b6efa2945892c1421498f725550fe78 |
| tests-r3/resource.json | 9d39f788371171b2a7e771d530be02a48f0e15a14b47fc63f2a3f02f7293a234 |
| tests-r4/command.log | 739b29a04777a46df5cc67b74146eb6cfa12dbecff725017929c37de911a516e |
| tests-r4/resource.json | 66d832886c8cd4beca1b40c12089228b907c8a2a24588a4907d1bac2d8664b53 |
| neighbor-r1/command.log | bae472ea4542694b5bebb26432cd98b47ade38545e5ab4a4cd535fbdd323c036 |
| neighbor-r1/resource.json | 173da10b0662207f982c46eaadd571e30f9131b87048bc2501d76a0c7e1b1346 |
