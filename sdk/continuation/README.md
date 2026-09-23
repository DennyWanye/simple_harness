# 尚未应用的独立源码

`nanojev-development.patch` 保存原 SDK main checkout 的已提交+未提交 NanoJev 源码、测试及架构文档，相对 `nanojev-provenance.json` 的公开 base commit。它保留独立工作，**并未应用到当前 Assurance SDK**。

NanoJev Primary 已移出当前 HTN/TaskGraph/Assurance 阶段；不要为了“全部合并”重开它，也不要直接向已含 HTN/TaskGraph 的 event_handler 套整份补丁。未来明确恢复该范围时按模块三方移植，并独立验收。
