# 夜间车道 N3a 记录（2026-10-07 夜）

工作树 `simple_harness-n3a`，分支 `night-n3a`，基线 main `47bf424a`（投递泵修复之后）。
条目：N3-27（隔离补三个写入口）、N3-30（隔离用例补 `drain()` 返回值断言）。依据《夜间-N3-建议裁决.md》第二节。

## 一、N3-27 隔离补三个写入口

隔离判断仍只有一个：`Orchestrator.recovery_isolated(mission_id)`。三个新入口都只读它，没有第二份名单。

| 入口 | 改法 | 文件 |
|---|---|---|
| 部署职责 | 自动确认完成要求、自动签规划授权、投影检查策略（范围 / 根终审 / 操作两审 / 做法计划）四个循环，遇到被隔离任务直接跳过，不发命令 | `SDK/deployment/duties.py` |
| 恢复第 4 步 | 启动绑定故障属于被隔离任务时，不调 `_round_fault`、不停任务；只记进这一步结果的 `startup_faults_isolated`（任务号、位置、异常类名）。`startup_faults_settled` 只数真正结算的 | `SDK/orchestrator/recovery_coordinator.py`（含模块文档第 3、4 步说明） |
| 门面 | 新增 `_refuse_isolated`：被隔离任务报 `MISSION_RECOVERY_ISOLATED`。别人的任务照样读作 `not_found`，不泄露存在与否 | `SDK/api/facade.py` |
| 拒绝码 | `RecoveryBoundaryCode.MISSION_RECOVERY_ISOLATED`，归"来源不可用"类（与降级恢复、禁副作用同类） | `SDK/contracts/error_table.py` |
| Host | 码本来就原样透传（`FACADE_CODES` 里没有的码原样给）。只加一张"码→给人看的话"表：这个码的文字换成已有的 `RECOVERY_ISOLATED_NOTE`。IPC 回执的 `error_code`、主对话 `mission_amend` 的拒绝码都是它 | `backend/deskpet/orchestration/service.py` |

### 取舍（自己定的，写在这里）

1. **门面拦哪些。** 规则只有一条：被隔离任务只接受取消。拦下的写入口：改要求、确认完成要求、规划授权的签发 / 绑定 / 续期、回答规划提问、审批决定（批准、拒绝、审阅、仲裁都拦）、接管、裁定结果不明的动作、登记 / 换版 / 撤销资料、评论、提交操作、批准检查策略。
2. **规划授权的撤销不拦。** 撤销是收回，与取消同向，不推进任务。
3. **评论也拦。** 评论不推进任务，但规则写成"只接受取消"最简单，也最好解释。界面上被隔离的任务本来就只给"取消"。
4. **部署职责与门面两层都读同一个判断。** 只有门面拦，部署职责也写不进去。但它每轮都会撞一次拒绝并打日志。所以部署职责自己先跳过，与主循环其他消费者一样。两层读的是同一个函数，不是两条路径。
5. **Host 只改到透传为止。** 码不用改。文字换成 Host 那句"重启核对没通过，已隔离，不再推进；可以取消"（裁决里写"可取消"，Host 现有常量是"可以取消"，沿用常量）。

## 二、N3-30 `drain()` 返回值断言

`test_recovery_coordinator.py` 里每个 `await world.drain(...)` 都断言了返回值。先打日志看实际值，再逐处判断该不该空闲：

| 位置（改前行号） | 局面 | 断言 |
|---|---|---|
| 99、106、121 | 健康任务做完 / 重启后空闲 | `is True` |
| 137 | 第一座世界，执行者回合被扣着 | `is False`：有在途回合，到时限也不空闲 |
| 174 | 重启后等健康任务做完 | 原来时限 10 秒，实测前三次都是 False。**不是隔离造成的**：把"改坏任务"那步去掉重跑，耗时一样（约 35 秒）。原因是健康任务的执行者回合是上一个进程留下的，要等它收回再重派。时限放宽到 60 秒，断言 `is True` |
| 178 | 健康任务做完后再跑几轮 | `is True` |
| 214 | 降级用例的第一座世界，执行者回合被扣着 | `is False` |
| 233 | 降级恢复后再跑 | `is True`：不进周期，`run()` 直接回来 |
| 247、260 | 健康任务做完 / 崩溃后接管 | `is True` |

**留给主会话（不是缺陷，供参考）：** 重启后，上一个进程留下的在途执行者回合要约 30 秒才收回重派。这是现有行为，本车道没动。

## 三、用例

- `T/full_target/test_recovery_coordinator.py::test_an_inconsistent_mission_is_isolated_and_the_others_go_on`（扩充）。被隔离任务从一个变成三个：执行到一半的、等规划授权的、完成要求还没确认的。重启时用自动模式，给执行到一半的任务塞一条启动绑定故障，再在部署职责的命令口子上装记录器。断言内容：
  - 三个任务都被隔离，健康任务照常做完；
  - 第 4 步把故障记进 `startup_faults_isolated`，`startup_faults_settled == 0`，没有 `MissionRoundFault`；
  - 部署职责没替被隔离任务发过一条命令，但替健康任务照常发了；
  - 足迹没变。足迹改成"每张带 mission_id 列的表的行数 + 派发意图状态 + 保证通道待办原样"，比原来只看事件数更严；
  - 门面：改要求、确认完成要求、提交操作、登记资料、批准检查策略、评论、规划授权签发、接管，对被隔离任务都报 `MISSION_RECOVERY_ISOLATED`，库里什么也没写。同样的请求发给没隔离的任务，报的是别的码，评论照常写上；
  - 取消三个被隔离任务都成功，之后主循环能空闲。
- 新用例 `test_an_isolated_missions_question_and_approvals_are_refused_and_taken_again_once_it_is_not`：回答规划提问、审批决定（批准 / 拒绝）。被隔离时报码，提问仍待答，审批单仍待批。解除隔离后，同一个回答照常收下。
- Host `backend/tests/orchestration/test_recovery_isolated_host.py` 新增一条：IPC 确认完成要求报 `MISSION_RECOVERY_ISOLATED`，文字是 `RECOVERY_ISOLATED_NOTE`；主对话 `mission_amend` 拒绝码相同；没隔离的任务不报这个码；IPC 取消照常。

## 四、测试数字

- SDK：`test_recovery_coordinator.py`（7 条）+ `test_error_table.py` + `test_round_faults.py` 里"启动绑定故障只停它所在任务"那条（不隔离时照常写故障）：**22 passed**。
- SDK 门面写入路径相关：`test_requirements_amend.py`、`p33/test_p33_sources.py`、`test_repeated_planner_question.py`：**90 passed**。
- `host_support/test_facade.py` 有 17 条红（还有 `test_completion_spec_approval.py`、`test_htn_deployment_wiring.py` 一起跑），**基线 `47bf424a` 上同样 17 条红**（报错是 `Orchestrator needs the deployment's native runtime profiles`，用例自己造的编排器缺执行池配置），与本车道无关。
- Host（`PYTHONPATH=.:../sdk/simple-harness-sdk/src`，加一个只在草稿目录里的 pytest 插件放行执行图部署验收门，做法同车道 R，插件不入库）：`test_recovery_isolated_host.py` **3 passed**。用已装的 opt.166 轮子跑，新那条是红的（`invalid_request` ≠ `MISSION_RECOVERY_ISOLATED`），要等 SDK 发版、Host 钉新版后才绿。

## 五、改坏

每次都先用 cp 备份，再清 `__pycache__`、重生成部署清单；恢复后再清一次、再生成一次。

| 改坏 | 结果 |
|---|---|
| 部署职责四个循环删掉隔离判断 | 红：记录器里出现三个被隔离任务（门面拦住了，所以没写进库；日志里是撞拒绝的告警） |
| 第 4 步不看隔离（`if False`） | 红：`startup_faults_isolated` 不存在 |
| 第 4 步记了隔离、仍照样调 `_round_fault` | 红：`startup_faults_settled` 1 ≠ 0 |
| 门面 `_refuse_isolated` 直接返回 | 两条都红：改要求报的是 `AMEND_REQUIREMENTS_STALE`；回答提问没被拒 |
| `_has_inflight` 不跳过被隔离任务（N3-30） | 红在新断言第 221 行（等健康任务那段的 drain）。把这条临时去掉再跑，红在新断言第 227 行（做完后再跑几轮的 drain）。两处新断言都能抓到 |

## 六、留给主会话

1. SDK 发版、Host 钉新版后，Host 新用例才会绿。
2. 《AGENT_ORCHESTRATION.md》隔离条目的写法归 N3-15（文档车道），本车道没改。
3. 重启后旧在途回合约 30 秒才收回（见第二节），供参考。
