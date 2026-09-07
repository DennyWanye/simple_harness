# Procedure public applicability：固定源码结果

最后更新：2026-09-06。执行源码 **ad189f523d35779691e4f7f3ce3875dc20811d1c**；
base5819b15e，复用source-oracle树/feat/typed-recall-procedure-public。
Dirac固定源码限定ACCEPT后执行；两批无源码漂移，首轮即绿，无新增red/fix故事。
旧626/fbeb setup BLOCKED原证据保留，source10没有重跑，不并入新全401结果。

## 实际结果

| 执行 | 结果 | PGID/退出 | 峰RSS | 资源耗时 |
|---|---|---|---:|---:|
| 必要公开测试 | 3 passed in 1.09s | 33458 / 0 | 76560 KiB | 1.303s |
| 原四格正式、非observe | 4PASS/0FAIL/0BLOCKED，4OBSERVED | 33474 / 3 | 135584 KiB | 0.877s |

正式Run `3913be071c484d069b48082fc5cec12a`；四格如下，无dependency格：
- eligibility/procedure-active
- eligibility/procedure-eligible-state
- eligibility/epistemic:procedure:explicit_user:source_bound
- eligibility/epistemic:procedure:explicit_user:user_confirmed

其余397未选，整体/层仍NOT_RUN/BLOCKED；exit3不是这四格失败，也不是全401通过。
3个测试方法内覆盖真实公开正控/错fingerprint、close/reopen新请求与exact重放零candidate reads、
原eligible literal→draft/授权REVISE→eligible_for_activation→snapshot（不晋升），
以及target revision/foreignRun/假terminal篡改精确命中intent检查。不得把其中子断言另加成pytest数。

## 命令与环境

沿既有installed H073/M0613解释器，无新venv、SDK source overlay、SQL、模型、native或build：
`/Users/denny/projects/simple-harness-memory-sdk-typed-short-sources/.local-test-evidence/2026-09-06/typed-short-sources/artifact/venv/bin/python`。

两批均经145默认共享OS锁入口，2048MiB/180s，未覆盖lockfile；本次无BUSY。
入口 `/Users/denny/projects/simple_harness-primary-candidate/scripts/run_resource_bounded.py`。

必要测试child命令：
```sh
<installed-python> -m pytest testcase/human-memory-program/tests/test_typed_recall_procedure_public.py -q -p no:cacheprovider
```
环境 `PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`。
正式child为 `run_typed_recall_public_consumer.py --consumer-python <installed-python> --child-timeout 150`
加原四个 `--cell`、固定H073/M0613 source/wheel/pin和fresh ignored artifact-dir；无--observe。
完整可复核argv保存在各批command.json，编排脚本run_selected.py位于下列ignored根目录。

## 证据索引

本树 `.local-test-evidence/2026-09-06/procedure-public/`，raw不进Git。

| 相对文件 | SHA-256 |
|---|---|
| tests-r1/command.json | 16bbd3a75ead091fbc2b7350dd1ee25161906e31368ed9eefdb2fe3ecb3e3e7b |
| tests-r1/resource/command.log | 2fac095b607a3098c31bd16abea685565efb1dd7ceed3938c4d2c4d6c27a421f |
| tests-r1/resource/resource.json | bf601b482169ad18f0b798793dc1ea23d53a1330b0696c07b73b7f62a5aa0390 |
| tests-r1/source.txt | dbc98fdc78eff451dca1fa251fa542358b32e2a86b6efe8547e68e7f969c99ef |
| formal-r1/command.json | 68a92c7345aa6a12142c930e83c507476874a1e0f0867d5251435aef8a41c28e |
| formal-r1/public-data/bridge-summary.json | 55b2e9dd8943832f20cc89a024ec03c949d39f15ee4cd8b533c5dacc6fee6fc7 |
| formal-r1/resource/command.log | 5cadb90ef9c20af7c2fc44378937449879923368752108b1bdf4fe03ef792358 |
| formal-r1/resource/resource.json | 6692b7c3d73f01c63e8951b82a127834b409a6c5477c3f876f492ea7005338cb |
| formal-r1/source.txt | dbc98fdc78eff451dca1fa251fa542358b32e2a86b6efe8547e68e7f969c99ef |

两份resource receipt均stop_reason=null、remaining_group_members=[]、cleanup_error=null；所有自有测试进程已退出，槽释放。

## 边界与交付

只关闭上述四格及必要公开反例。observed_behavior真实terminal晋升、32禁止epistemic组合、
Prospective、完整Procedure projection、401完整新run、质量/Provider/native均未由本片证明。
原source10与旧public182不同执行源码/Run，不能把本4格简单相加宣称新总计。
Hegel context-use合并时bridge应同时保留source/context_use/procedure oracle指纹。
ARCH仅记录本测试工具叶子结果，不宣称改变Host或SDK生产能力。
