# Source10 successor preparation

状态：固定65990a68 source10已实际执行通过，独审结果待回。以下NOT_RUN段为准备历史。
主native/build独占共享锁，未启动pytest/新source run/模型/native；没有新PASS。
原626/fbeb的source10仍0PASS/0FAIL/10BLOCKED。

契约见[CONTRACT.md](CONTRACT.md)。固定source后等主释放统一slot，
再跑必要真实10格与独立反例；新结果按新源码单列，不覆盖旧证据。
`git diff --check`无空白错误，仅静态检查。

## Dirac只读挑战后继（仍NOT_RUN）

2b17未获ACCEPT：P1原证据错绑自洽hash、P2 request非strict JSON。
后继补原admitted+mutation spans回绑及交换/复用来源重hash反例、schema2/extra-key反例。
主已明确原CONFLICT_GROUP_CORRUPT语义类别映射；代码实际读取冻结expect/reopen/零披露字段，
保留真实outer/cause/frames，不修改SDK或fixture。
尚未执行正负例；fixed源码复交Dirac，只读不占Singer/native槽。

Dirac后继只读发现expected semantic source遗漏semantic_kind=claim；已补冻结字段，保留全内容/hash比较。仍NOT_RUN。

首轮e024测试1FAIL及正式9PASS/1FAIL：three-member实际probe cause为foreign key check failed，旧oracle猜成integrity cause。保留两份红证据（tests-r1及本树typed-recall-0613-formal/s00），修正exact cause并同时要求实际CHECK/FK损坏；非SDK修改。

## 2026-09-06 固定65990a68实际执行结果

- e024首轮tests-r1：1FAIL/1.95s。正式旧s00：9PASS/1FAIL，three-member cause预期错误；两个原红保留。
- c9bd618e只修真实FK cause，并同时要求CHECK+FK的实际损坏；tests-r2 1PASS/2.18s。
- 65990a68再收紧schema2/extra-key必须命中strict request schema目标reason；tests-r3 **1PASS/2.15s**。
  这是一个集成test，包含10格真实source正控、8个faultstate篡改、18个corruption篡改、
  2个strict request反例、2个交换/复用source且重算hash的反例（30个反例检查），不能报31/40个pytest。
  swap/reuse必须命中original distinct member evidence IDs differ；schema2/extra-key必须命中
  durable request exact schema/keys differ，均非无关早期异常阻挡。
- 正式source10复验固定65990a68：**10PASS/0FAIL/0BLOCKED**，10格均OBSERVED。
  391public全部NOT_SELECTED，summary NOT_RUN/BLOCKED/exit3；旧层transport status也保留NOT_RUN/BLOCKED，
  此处10PASS严格指selected-cell独立oracle计数，不宣称全401/quality/program/native通过。
- 不与626/fbeb的182public混算新全量；旧source10BLOCKED与e0249/1原始证据完整保留。
- SDK仍installed H073/M0613；source checkout exact f2 clean，无SDK修改/重建/模型/native。

## 命令与资源

测试命令由 `/Users/denny/projects/simple_harness-primary-candidate/scripts/run_resource_bounded.py`
固定145baed3包裹，默认共享OS锁，`--rss-mib 2048 --seconds 180`。
解释器为M0613 artifact ownvenv；`PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`：
`python -m pytest testcase/human-memory-program/tests/test_typed_recall_source_oracle.py -q -p no:cacheprovider -p pytest_asyncio.plugin`。
正式命令完整argv保存在下列formal-r2/s00-command.json；未使用observe。

| Batch | Source | PGID | Exit | Peak KiB | Resource seconds | Result |
|---|---|---:|---:|---:|---:|---|
| tests-r1 | e02412fd | 30527 | 1 | 105440 | 2.188 | 1FAIL |
| formal-r1 | e02412fd | 30561 | 1 | 145232 | 2.386 | 9PASS/1FAIL |
| tests-r2 | c9bd618e | 30606 | 0 | 111200 | 2.382 | 1PASS |
| tests-r3 | 65990a68 | 30634 | 0 | 111264 | 2.375 | 1PASS |
| formal-r2 | 65990a68 | 30659 | 3 | 157920 | 2.391 | 10PASS |

全部remaining_group_members=[]/cleanup_error=null/stop_reason=null；所有进程组结束，slot释放。

## 本机证据索引

- `.local-test-evidence/2026-09-06/source-oracle/tests-r1/resource.json` SHA256 `48db416db4d9f95cef9ae2ac97193ce9dd1b05753c8971e07805549b16ad2984`
- `.local-test-evidence/2026-09-06/typed-recall-0613-formal/s00/resource/resource.json` SHA256 `7176b70127a95e3b03a4cbef1119949e39c688b2184470eac40c92947443846e`
- `.local-test-evidence/2026-09-06/source-oracle/tests-r2/resource.json` SHA256 `228cbec8528ee4b9f323cb91e5676a5afbe0dfcb1c39126d6c2e387e7d84455d`
- `.local-test-evidence/2026-09-06/source-oracle/tests-r3/resource.json` SHA256 `64897417dbdaa6c1ae35304d0b0169dc83d1f9eec86afa54a9eee196222e861e`
- `.local-test-evidence/2026-09-06/source-oracle/formal-r2/s00/resource/resource.json` SHA256 `c3b71a13d4bf8a7eff45be996331c34209847ef7058e016a9debc66bbbb1925c`
- `.local-test-evidence/2026-09-06/source-oracle/tests-r3/command.log` SHA256 `484714eb4f0e6308d329b77c2b018b4f0c941a20f98722f0157d2c404dbbc11a`
- `.local-test-evidence/2026-09-06/source-oracle/tests-r1/command.log` SHA256 `c7a2f5da2ce8c307a217d429810009b9a22b2c6e6ea84e1466b36c0f1645fecc`
- `.local-test-evidence/2026-09-06/source-oracle/formal-r2/s00-command.json` SHA256 `afeae4a4b7cc1fa3ed3640ee09465f6c9d860c917e55fe6bbdba56e662e60947`
- `.local-test-evidence/2026-09-06/source-oracle/formal-r2/s00/public-data/bridge-summary.json` SHA256 `bba5159a623afd9b5fd0e8ee1ec7f02eef75c9457cb660c45fcd0b42a3f159b1`
- `.local-test-evidence/2026-09-06/source-oracle/formal-r2/s00/public-data/source-observations.json` SHA256 `2c8315eb055bdf215b166146fb65acfc6e39e361d1096f54ce59bf015c1659ed`
- `.local-test-evidence/2026-09-06/source-oracle/formal-r2/s00/public-data/source-runtime.json` SHA256 `31cb61a85d97aa0afd0d0cb1c903d387b62e19f2169993739a79bfeb489f809f`
- `.local-test-evidence/2026-09-06/source-oracle/formal-r2/s00/public-data/source-request.json` SHA256 `39c043df68f72270b0311fc67124df0723e756f9b4c0caedd5db51e2d3a0d709`
