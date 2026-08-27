# Testcase inventory 与复用报告

- Inventory：`testcase/index.json`，本轮 build 后共 122 条；新建 5 条 active revision 1 用例。
- 已逐篇阅读最接近的通用能力安装、SDK Capability catalog、SkillsView，以及 Project 注册/重启/projectless/authority/root split/relocation 候选全文。
- `LEGACY-A48A2DC10836`、`LEGACY-C85B8602F960`、`LEGACY-29441499FCB9` 均为 `needs-review`，不可直接选入；其 oracle 只作为新用例增量设计依据，不继承历史 PASS。
- TC-PS 系列保持 active，但入口不包含 managed Skill 安装；对 Project authority 相关义务采用 `reuse-with-extension`，selected asset 仍是本 slice 的 TC-SI 用例，避免改写旧 oracle。
- 逐 obligation 决策与 revision lock 见 `testcase-reuse-report.json`；没有 retired/superseded replacement 断链。

