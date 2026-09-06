# 后续测试资源管理

最后更新：2026-09-06。用户内存不足事件的持续修复；本工具不调用plan-test、不运行其机器门。默认2GiB RSS/180秒，跨本用户的工作树用同一OS文件锁串行；占用时返回75且不启动子进程。必须给新的ignored证据目录，不覆盖历史失败。正向保留命令退出码；资源超限、超时、信号或父退出后残留子进程均返回125。

`start_new_session=True`创建本次独立进程组，周期读取该组RSS；无论父进程是否已退出，finally均先TERM、再KILL并复查剩余组成员。SIGINT/SIGTERM触发清理，二次信号在清理期间忽略，结束释放文件锁。不会按应用名或全机PID清理，不关闭用户ChatGPT/Code/Chrome/UU。原始输出与资源收据仅写本机证据目录，收据不记录命令参数或异常正文。

边界：仅POSIX/macOS/Linux；不是OS硬内存限额，采样间可暂时超限，短瞬时峰值可能漏测。不追踪主动setsid脱离的后代，不能保证包装器被SIGKILL/机器断电后的清理；不实现模型权重卸载、应用内存泄漏修复或全系统压力监控。进入重型native/模型验证前仍独立核查系统压力；保持单实例和退出后的进程检查。

## 使用

从相应工作树执行（选择准确安装环境的python）：

```sh
python scripts/run_resource_bounded.py   --evidence-dir .local-test-evidence/2026-09-06/<new-batch>   --rss-mib 2048 --seconds 180 --   python -m pytest -q <changed-risk-tests>
```

`--lock-file`默认`~/.cache/simple_harness/test-resource.lock`，正式批次不要通过改锁文件绕过串行；仅工具自身隔离测试使用临时锁。保留原来的模型禁止导入策略；本入口只管理进程资源，不自动决定某个测试是否会加载模型。

## 实际验证

精确安装解释器：`/Users/denny/projects/simple_harness-model-short-recall/.local-test-evidence/2026-09-06/model-short/venv/bin/python`。这里只使用标准库，无SDK/模型/Provider/native。

- 运行本入口（256MiB/30秒）包裹`scripts/tests/test_run_resource_bounded.py -v`：6项实际进程测试通过，3.218秒；普通成功/原失败码、真实64MiB分配越限、超时、持锁拒绝启动、父退出且孤儿忽略TERM仍被KILL。单独启动的无关见证进程始终存活，由其测试owner收尾。外层PID25781已退出，采样峰值45440KiB（不等于瞬时最大或内层单项峰值）。
- 新增SIGTERM case单独运行：1项通过，0.070秒；实际运行中的包装器收到TERM后清理命令组，检查收据为空且OS锁能重新获得。外层PID25932已退出。
- 所有资源收据 remaining_group_members=[]/cleanup_error=null；测试辅助的短生命周期临时fixture不是产品数据。两批分别记录，不冒称全套重跑；git diff --check通过。

## ignored证据

根`.local-test-evidence/2026-09-06/resource-runner/`：

| 文件 | SHA256 |
|---|---|
| first/command.log | 342a4e890b45a2058938eb2fdca53f5626320619987fc7321d3859f59864c61b |
| first/resource.json | 417e065521ee278d058bd724502f9cd343b809a20ff6e5bda09d4b6a0d9561dd |
| signal/command.log | aeef17da861cb8a9ba7f4616f4e3c941eae8bbffe6ef54a4931e891d3eee4f8b |
| signal/resource.json | 97f3af13dd784218725e9f28d7d16955d10f82766b33d68749695f5c51722d24 |

## probe异常后继修复

主复核发现固定e2f6d7ce中ps probe异常会使TERM之后跳过KILL与reap。使用旧固定源、真实忽略TERM的子进程和注入的ps异常，反例明确FAIL（PID26358仍运行，测试finally自行清理）。修复后cleanup probe异常仍强制KILL本组并独立wait；保留cleanup_error，资源判定仍125，不伪装已核查清理成功。

同反例加孤儿/SIGTERM两个必要邻居：3 PASS/2.493秒，外层PID26373峰值49904KiB/exit0。内层故障测试parent_returncode=-9且独立os.kill(pid,0)确认为不存在；cleanup_error=RuntimeError表示探测证据缺失，与内层进程已被实际杀掉的测试证据分开。原6及追加信号绿未无意义全量重跑。本轮实际唯一8个功能反例/边界，非8项一次运行。

| 后继相对文件 | SHA256 |
|---|---|
| probe-red/command.log | c270be8292caae6c6a14b160a8a04f3fcac8890226b4d9e59b63f383f2afdb77 |
| probe-red/resource.json | ad9fccb4924eeed957eac0821dc9056fa14038a7fb98238f522a58d2fd68fd91 |
| probe-fixed/command.log | 80151bc0bf8dda1a0a02d6364fe8895a9e45a4ae552b62f5477895efbf33813e |
| probe-fixed/resource.json | 1bb7d681346d706663a253a88fa0e6bff2569d233e1d240eb7b23fa224f6172c |
