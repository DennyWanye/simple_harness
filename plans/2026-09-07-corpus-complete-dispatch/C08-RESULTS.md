# C08 正式评分入口：首次组合验证

2026-09-07，主e81a9af7（源39356e1d）+提取helper f45da5f9（8c238e21），当前H0710/M619/S0313。唯一新dispatcher控制 **1 PASS / 8.00s**，原retained五控和13 scalar控未重跑。

正式prepare_batch仅开放C08-01/06/11/18 retained；C08-01不再先跑不完整scalar预seed。沿共享run的一次actualmain初始化及原runtime，loopback旧组→真实USER job/APPLIED/IDLE→公开抑制→原production authority重开CONFIRMED之后，才准入独立评分Provider并发送原current。此次实际走C08-01的正式分派，核下一物理HTTP不含旧USER、摘要、setup及旧value；setup/current Run与Provider invocation不同，实际评分trace只含1次调用，review_packet统计也仅1次。

当前回复由受控HTTP提供；没有真实模型、gold评分或原生操作。四IDs的公共分派已接，当前正式入口组合仅01单控，另三类型此前有独立retained-main准备/隔离证据；其他C08仍blocked。不能把本结果计为240质量通过，也不宣称全部C08/rolling-summary/short generation完成。

本批包括34b6d8eb：把诊断await移动到main已发布SDK stack/ingress所有者之后，确保该新增取消点下普通关闭路径可找到已启动owner。未新增常驻任务；本批实际启动与自然退场通过，未额外跑取消故障或旧绿色组合。

共享资源PG85743，exit0/8.695s/peak545168KiB/minDisk4410MiB/remaining=[]/cleanup=null/stop=null。child自然退出，无资源阻断。防熄屏保持。

命令：当前installed target优先PYTHONPATH，经run_resource_bounded.py --rss-mib 2048 --seconds 180；pytest -q backend/tests/quality/test_corpus_c08_phase.py::test_actual_dispatcher_retained_phase_then_separate_scoring_request，basetemp位于本批ignored目录。

| 本机原始证据 | SHA-256 |
|---|---|
| `.local-test-evidence/2026-09-07/corpus-c08-dispatch/r1/command.log` | `7ec587dca5b2136fa1615d4999b63f5e529cd16fa2712d9491d2fa727d95c126` |
| `.local-test-evidence/2026-09-07/corpus-c08-dispatch/r1/resource.json` | `11370340111201681962795b6425f28f167dd01246117efd81c061abb435f7fe` |
| `.local-test-evidence/2026-09-07/corpus-c08-dispatch/r1/tmp/test_actual_dispatcher_retaine0/.local-test-evidence/retained-dispatch/C08-01/execution.json` | `29a467ecae5e3d2e8709748c41ef45075de622c3b75926e66fd323550f31d61c` |
| `.local-test-evidence/2026-09-07/corpus-c08-dispatch/r1/tmp/test_actual_dispatcher_retaine0/.local-test-evidence/retained-dispatch/C08-01/setup-retained/phase.json` | `1c75440f95296bb51304dc61da347f21394e281159310bb143ee09000520f56d` |
| `.local-test-evidence/2026-09-07/corpus-c08-dispatch/r1/tmp/test_actual_dispatcher_retaine0/.local-test-evidence/retained-dispatch/C08-01/review-packet.json` | `555054dea8f84b0fd2abd81bc668330b389c0b4c7759d05155b6b1563a072166` |
