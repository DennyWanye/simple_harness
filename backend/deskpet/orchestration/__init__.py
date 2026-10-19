# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""桌面端对 SDK 任务编排宿主（``agent_orchestrator.host.OrchestrationHost``）的接线。

拼装、驱动与命令都在 SDK 宿主里；这个包只管桌面自己的：配置段与提供方链快照、钉版核对与部署清单、
控制通道协议、进度推送、通知存档、界面投影与诊断。见 ``ARCHITECTURE/AGENT_ORCHESTRATION.md``。
"""
