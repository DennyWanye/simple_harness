# 自我批评（Slice 1，2026-09-10）

- **真拦住问题的环节**：第 1 轮 primary 挑战。它用一条 SQL 证明「模型视野」在改动前就是空的，把主要矛盾从「摘掉模型视野」纠正为「守卫链同步 + 不误伤机制」，并证伪了 `operation-audit.db` 17→0 那个永远到不了 0 的锚点；两个 P0 都是主 agent 自己没跑命令、凭读码估算造成的。closure 两轮又抓出集合基数（20 不是 24）、成对计数行号（:297）、空投影下断言抛 KeyError 三条真错误。
- **空转 / 拖慢的环节**：specialist 派发规则。四个 required cluster 里三个的证据 closure 已经拿全，为了满足「不得自行降级」仍补派了一个 specialist；它确认了对齐但新发现只有两条 P2 的 oracle 加固。对一个删除任务，这一步是仪式成本。规则改进方向：closure 用真实命令复核过的 cluster 应可记 completed，不必另派。
- **仪式之外的真收益**：把切片裁决交给独立评审，避免了「整条线一次删完」——Host 侧 `effects.py → store → checkpointer → human/native/outbox` 的穿透和 SDK 侧 checkpoint 字段的破坏性，任何一条都会让冒烟在第一轮死掉且无法归因。
- **仍然欠的**：`turn_preparer.py` 的 `self._profile_registry` 字段留成了只写不读的残留；三个拒绝集与 deny_selectors 还带着旧名字，都挂在 F-WF-1 上等用户决定。
