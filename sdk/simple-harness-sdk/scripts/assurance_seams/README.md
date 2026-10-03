# 小范围接线诊断源码

仅用于主体编码过程中解决具名接线问题；禁止批量运行这些脚本并当作产品验收。

从 Host 仓库根执行：

```bash
uv run --project sdk/simple-harness-sdk --frozen sdk/simple-harness-sdk/scripts/assurance_seams/critic-format-repair-seam.py
```

七个接缝都起在产品同形世界上（`agent_orchestrator.testing.product_world`：产品那一份部署组装、保证通道、原生执行池、主循环真跑），共用种子是 `_product_seam.py`（取代已删除的 `_assured_fixture.py`）。替身只有外界会发生的事：脚本化的模型回复、产品自带的崩溃点、磁盘上的字节变化、部署策略里的真实授权时长加真实等待。每个脚本最后一行打印 `{"status": "PASS", "evidence": 证据路径}`。不证明真实模型、Host 或界面。

脚本由本机历史诊断源移植，路径改为仓库相对定位，输出保存到根 `.local-test-evidence/<日期>/assurance-integration/`。2026-10-03（HTN 补齐阶段 A′）：只保留有测试调用的 7 个接缝（critic-format-repair、evidence-tools、executor-check、final-writer、four-consumer、recovery、root-gate），没有任何调用方的 10 个已删除；保留的 7 个随阶段 A′ 迁到产品同形测试世界（`agent_orchestrator.testing.product_world`）。
