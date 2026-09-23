# 本轮窄检查结果
工具 verify_delivery 与 check_plan 均 PASS；没有运行批量测试。
## 六个种子 hash
| SDK相对路径 | 本轮 SHA-256 | seed 一致 |
|---|---|---|
| src/agent_orchestrator/contracts/resolution.py | `92dcb60352ba829275bfe4c732691f20d63a97eee875edb98db81672461fad60` | True |
| src/agent_orchestrator/orchestrator/commit_service.py | `ad96a63e6b9bd042c23d61df535ed82d29bb96e2d5c87bd3a27175caf8e35edd` | False |
| src/agent_orchestrator/orchestrator/leaf_acceptance.py | `4762fa860e959fa0e51fba49f81e78c905a44eebd06e4e2e8f463d5c84b9ab8d` | False |
| src/agent_orchestrator/orchestrator/hierarchical_dispatch.py | `f3db70324195a24b780cf5ccb738415a78fc5f7e7c29f5f2607d57d809106a71` | False |
| src/agent_orchestrator/orchestrator/resolution_commits.py | `7b95a7d3aa8ff5422e9e6c6b52c3fd996e16e553bc630bb830196cd71ed24ef6` | False |
| src/agent_orchestrator/orchestrator/event_handler.py | `4610d3f71a4dd574d2c7f42d1eacc434ee1942305fa4535844d8e09ba92646a6` | False |

## 字段源表与 Schema 不一致的 15 项
- `contracts/check-binding-v1.schema.json` → `execution_ref`
- `contracts/check-binding-v1.schema.json` → `evidence_refs`
- `contracts/closeout-v1.schema.json` → `unsettled_operation_refs`
- `contracts/closeout-v1.schema.json` → `accounting_pending_refs`
- `contracts/closeout-v1.schema.json` → `dangerous_work_refs`
- `contracts/closeout-v1.schema.json` → `report_ref`
- `contracts/review-binding-v1.schema.json` → `subject`
- `contracts/review-binding-v1.schema.json` → `check_requirements`
- `contracts/review-binding-v1.schema.json` → `evidence_catalogue`
- `contracts/review-binding-v1.schema.json` → `budget_reservation_ref`
- `contracts/review-record-binding-v1.schema.json` → `reviewer_turn_ref`
- `contracts/review-record-binding-v1.schema.json` → `raw_output_ref`
- `contracts/review-record-binding-v1.schema.json` → `consumed_check_refs`
- `contracts/review-record-binding-v1.schema.json` → `exposed_evidence_refs`
- `contracts/use-certificate-v1.schema.json` → `clean_support_refs`

## SQL 定点反例

均使用测试父表和内存数据库，仅证明包内 DDL 防线不足：直接 INSERT FINALIZED、FINALIZED DELETE/重建、无 review 的初始 BOUND pin 均被接受。未测试 SDK Store，不构成当前产品可利用漏洞声明。
