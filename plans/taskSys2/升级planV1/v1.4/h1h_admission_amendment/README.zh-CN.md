# H1-H 三个 AdmissionContext Blocker 补遗

入口：[AMENDMENT.zh-CN.md](AMENDMENT.zh-CN.md)。这是正式规格补充，**不是 SDK 补丁，也不能直接 cherry-pick 到用户工作树**。

## 使用顺序

先读 §0–2、登记原任务书与本补遗的优先关系；按 §3–7 实施三条真实来源链；按 §8–10 完成 focused、独立核验、H1-I 和完整 H1 门禁。

`reference/rules.py` 是可执行的局部规则参考；`reference/schema.sql` 是新增表的 DDL 参考，下一迁移号和原表外键必须在本地核对。`sdk-test-cases.json` 给出36组真实 SDK 待执行测试。`sources.json` 保存固定源码及原规范来源。

## 可在本资料包内执行

```bash
python -m unittest discover -s tests -v
python validate_kit.py
```

这些命令不启动 SimpleHarness、模型或外部服务，不证明本地 producer/Commit/回放已经通过。真实验证边界见 [VALIDATION.md](VALIDATION.md)。

## 禁止误用

参考 ActionRow 的证据布尔字段必须在真实 adapter 中由身份匹配和证据校验导出；测试中使用的 True/False 是固定案例输入，绝不是允许生产默认填值。真实 COMPLETE_EMPTY 必须来自全量一致读取；参考 complete_read 参数不能替代生产读取器。

不改变 decode-only 裁定；NanoJev/Shadow 不可用来替代 H1 的正式准入。真实模型仅使用既有获准 GPT-5.6 系列配置。
