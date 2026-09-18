# 审计包 2026-09-18

本目录是 FULL-TARGET-1.4 / Grok 真实验收（第 1–6 批）的只读审计入库，指挥者已检查并提交。

- `00-审计总册.zh-CN.md`：计划、用户决定、批次台账、修补切片、runner/Host 变更、事故、证据位置、续跑方法。
- `episodes.csv` / `build_episodes_csv.py`：只读遍历全部 `result.json`（含 superseded / invalid；跳过 receipts 副本）。
- `sha256-manifest.txt`：每个入库口径的 `result.json` 与全部 `orchestrator.db` 的哈希与字节数（DB 只哈希不复制）。
- `episodes/<batch_dir>/<episode>/result.json`：经密钥扫描后的副本；初版因规则误判漏掉的 30 份已在修正规则后补齐（真实命中 0）。不复制 DB、worktree、delivery。

证据原件仍在 Host `.local-test-evidence/2026-09-16/htn-acceptance/`（gitignored）。第 6 批 9 局已全部收入（重跑 `build_episodes_csv.py` 可再生成）。
