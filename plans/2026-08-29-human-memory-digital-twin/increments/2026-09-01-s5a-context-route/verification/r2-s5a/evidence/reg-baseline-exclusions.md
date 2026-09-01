# S5A-REG-FULL 基线排除清单（非 S5a 引入）
7 项本机环境红：capabilities×2、capability_owner_scope、host_control_downgrade、agent_storage_reset、health_check_timeout、process_list（flaky）。
证据：S4 期全量 log 同名单 6 红；主干 04a5a649 + memory-0.5.2 对照复现（2026-09-01 会话记录）。判据 = 不低于基线：6272 passed 零新增回归。
