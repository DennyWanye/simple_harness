# STATUS 索引

> 本目录存放 DeskPet 的**状态档**——一页式看清项目/模块当前进展。
> 新建状态档后请在此登记一行。功能落地状态以各档为准。

| 文件 | 作用 | 何时读 |
|---|---|---|
| [`status.md`](./status.md) | **全局项目状态**——所有并行 worktree / 核心功能模块完成度 / 最近里程碑 / 已知问题，一页看清整个项目。 | 新 session / 子代理接手项目前先读这里。 |
| [`DeepResearch.md`](./DeepResearch.md) | **deep research 模块专项状态**——`research_run` 管线与能力清单、plan vs 现状差距、已核实真 bug、调查盲区、演进建议。 | 要动 deep research / 深度调研功能前先读。 |

---

## 维护纪律

- 一个模块跑通验收（pytest/vitest/cargo/手工 E2E 全绿）= 完成 → 同步更新对应状态档（见根 `CLAUDE.md` §STATUS 更新纪律）。
- 细节放各 `plans/` 文档，状态档只记状态 + 链接，保持一页能看完。
- 新建专项状态档 → 在本 index 登记一行。
