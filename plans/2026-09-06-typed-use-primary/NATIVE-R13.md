# 原生r13：标准驱动审计核验

更新：2026-09-06。Host `b3680732d11460eb36677e1661b4d0988abbc700` / H078 / M617 / S0313，既有18ec前端/Rust二进制SHA `bdc57848a184a4da12832f93c3fb79419315cb839ae6aa7bbe4c07073c584901`。只验证新增驱动选择的普通主对话及其真实自动审计，不重复短召回场景，不计作24轮新旅程。

## 实际产品行为

用原生输入框发送“请计算37加8，简短回答。”，真实Provider最终回答“45”，UI回到空闲/排队0。首个AX按钮点击后截图显示输入仍在框中；改为可见坐标(1260,718)点击才实际入队，只产生一个新Run，没有模型重试。结果截图已滚动到底部，保留新用户句、真实回答与空闲状态。

- Host Run `f4370cbe-1a87-537c-8d3b-8e0abbf8bd16`。
- SDK Run `product-sdk-50a2b564ff3d7106779c895348a2529e075744d80954d74ec183302aadbfacf0`。
- 默认后台audit job `9696b6fc66af5fc498d2de22ade6b12a320d2e4e8acc43e4ba03448918a16ec2`：enumerated，45/45公开DTO行，last_code=null。
- 该SDK公共snapshot明确 `recording_coverage=verified_current_intervals`、`coverage_gaps=[]`、`history_coverage=recorded`；原历史限制 `legacy_unwitnessed_calls_not_reconstructable` 保留。
- 条目包括Context14、MemoryPort3、Provider4、Run10、Runtime14；这些是公开DTO行，不能当45次独立操作或4次Provider调用。没有tool/effect路径，本片不证明那些路径的新原生覆盖。现有规则findings为空。
- 真实SDK终态completed，terminal event sequence22，event record hash `bb2ac6a1d75acd53c2c8d822bda14b6baabaa5c9514edf6cccb51376a5eee711`。

这证明H078正式SDK选择器在真实普通Host对话中接通了驱动记录核验。旧r12的98条enumerated仍保留其unverified结果，不追认旧区间；不称全SDK操作覆盖、完整程序/质量验收已完成。

## 退出与只读诊断

保存AX/截图后正常CmdQ。PG99878/native99883/runner session78924正常exit0，159.648秒，peak1366112KiB，remaining=[]、cleanup_error=null；最少可用磁盘4937MiB，默认共享锁释放。测试效果和证据收集先于清理。

应用退出、audit库无WAL后复制做immutable只读诊断；源/副本hash均 `cd2e0b9f5771297175071ae9429c57ecd44fe6ce81343ea823badfe3b0945f85`，读取后源hash不变。未修改用户原库，也未主动重放audit worker。

launcher `.local-test-evidence/2026-09-06/native078617/launch_current_native.py` 仅把旧launcher的evidence/installed路径改成078；SHA `9626f4a74d18b8f0033d293f09e033380a9c336263e9500a081b8be32f21ab03`。仍Tauri管理唯一backend，使用原native userdata、existing primary-m0615 Python、新primary-078617 target；4096MiB headroom/8192MiB RSS/1800秒原生授权界限。

## 本机原始证据

根 `.local-test-evidence/2026-09-06/native078617/`。仅本文结论/索引hash提交Git。

| 文件 | SHA256 |
|---|---|
| primary-ui-cwaoe_s_/launch.json | c9469131fdcf8d3c98bd09f5f10815110b0fe0d53557920952cb11dfd633df83 |
| primary-ui-cwaoe_s_/ordinary-result.ax.txt | fe7338f36c181586bf5c97915e0d77d3fad601bc3738f2e4d4bb41849b777594 |
| primary-ui-cwaoe_s_/ordinary-result.png | 5a843920e635d4bd2633abf266c65ddbfcfe43671fbdaf0eff97e3e75affafdc |
| primary-ui-cwaoe_s_/native.log | 32f5b82c366b05ad8ff2d58d5f72b0b9a6803022c1c4e887f024db4c4ae06d4b |
| launch-r1/resource.json | e212f1d331da55ed745ddd69dc73e7608b907de2f63b9a9d1af6108f3389c60b |
| audit-r13-inspect/summary.json | b06d1f37bcd0e7c7d51ca4f790d78618302d2f7223ffbc014ab70651e6ac1d38 |
