# Testcase challenger Round 1

## 结论摘要

- 9 个 required testcase 的范围分解最小且无冗余，所有 AC 都有名义映射。
- FAIL：fixture 缺 exact member identity 与 N+1；Tool/policy 示例不固定；旧 projectless 与旧 Project Skill migration lane 缺失；安装失败 UI 未真测；fault/lost-ACK runner 与 `root_run_id` N/A 规则不明确；TC-GS-02/09 未兑现最小 root 数。
- 修订要求：不新增 testcase ID、不放宽 `verification/testcases.md`，只扩展现有 lane、固定 fixture/runner/evidence contract。

## 复用审查

- 已读 inventory、13 个候选全文、总 report 与三个 slice report。
- `create-new` 合法：旧 TC-PS/TC-SI 的 projectless/Project-scoped Skill 语义与批准目标相反，只复用 oracle，不继承历史 PASS。

## 最小充分性

- 当前 required：9；建议删除：0；建议新增 ID：0。
- 必须先关闭上述可执行性缺口才能判定为最小充分。

VERDICT: FAIL
