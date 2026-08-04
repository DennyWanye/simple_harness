# Plan challenge 第 2 轮

## 致命问题

1. 多个 workflow 表会原地更新，messages 还会归档；两个 max watermark 不是一致快照。需要每库一致读并生成不可变 projection manifest。
2. 跨多流 cursor 只有 last key 不足以恢复 reducer；cursor 还缺少请求绑定、签名、过期和错误语义。
3. 现有 Inspector 是定时发送、服务端串行 await、客户端只忽略晚响应；慢查询会排队，必须改成有服务端 task/cancel 的单飞协议。
4. Context history 目前会 `ON CONFLICT DO UPDATE`，sample identity/payload 冲突/CAS/晚到 lineage 没有闭环。
5. workload context 传参和 audit authority 仍留二选一；无 root 的 maintenance 不能直接落 execution provider ledger。
6. 删除 public provider/tool raw 字段和运行图语义会反转既有 UI/Inspector 测试，缺少独立 behavior change。
7. “106-record Godot fixture”和 maintenance 402 / child failure 的 DEV fault seam 尚不可复现。

## 真架构问题

- 跨库 Public Read Model 必须使用各库一致读生成的不可变 manifest，明确承认不具备跨库 ACID，而不是用两个 max id 伪造。
- Context Usage 必须以 immutable event contract + conflict detection + CAS reducer 作为唯一写路径。
- Inspector 必须把 snapshot refresh、detail page 和 cancel 设计成有生命周期的单飞请求协议。

## 次要问题

- 固定 migration 文件/版本、索引和 rollback test。
- `binding_only` stale 绑定需返回 `availability=unavailable`。
- totals 必须按 source/query kind 分项定义。

VERDICT: FAIL
