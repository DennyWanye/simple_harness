# Assurance 当前生产边界

最后更新：2026-09-23。跨电脑源码交付：完整 HTN + TaskGraph23 + Assurance WIP SDK 位于 `sdk/simple-harness-sdk/`；最终 TaskGraph Host UI2 源码位于 `development/taskgraph-host-overlay/`，尚未覆盖根 Host。根 Host 仍固定 HTN wheel。Assurance 主体、UseCertificate/acceptance/终态/生产装配未完成；仅局部 seam，不是整体产品 PASS。实际交接见 [HANDOFF](../HANDOFF-2026-09-23.md)，下文保留历史检查点。


SDK 源码已保存到私有主仓库，但新 lane 的 factory、四 consumer、完整 current Validity/UseCertificate、acceptance 和唯一 terminal writer 尚未部署。TASK_CONTENT 原 runner/collector/official 的局部 scripted 接缝已实现；不是新 Mission 默认启用的已完成能力。

全部剩余工作、代码入口、源码身份和下一电脑命令统一见根 HANDOFF；测试原始证据留在本机 ignored 目录。
