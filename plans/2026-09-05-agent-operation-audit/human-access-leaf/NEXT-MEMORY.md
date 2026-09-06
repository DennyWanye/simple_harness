# WeMM 按需加载：最小可行范围

2026-09-06，只读评估。Host 基准 `139997527ba5adbccd46c7b5a397646f071e040d`
位于 `simple_harness-primary-candidate`；SDK 依据该树 vendor 的 Memory0.6.12
wheel 内容，不将其他 checkout 的 site-packages 当作此候选源码。
约 6GB 为主协调实测，本次未启动模型、测试或测量 RSS，未修改产品代码。

**可行，但只改构造函数不能保证所有旧库的空闲启动都不加载权重。**

## 已核事实

- `backend/main.py:3327` 启动创建 WeMMEmbedder；
  `backend/deskpet/memory/wemm_embedder.py:34-48` 同步 import 并构造
  SentenceTransformer。模型尚未被用户使用，权重已经加载。
- 同文件 `:56-71` 的 dim 从已加载模型读取，lineage 又依赖 dim；不能把构造
  延迟后仍让这些同步元数据属性偷偷触发加载。当前契约为 WeMM、2048 维、L2、
  `product-bundled` revision 与现有 fingerprint，不应借优化换模型或改 lineage。
- vendor `simple_harness_memory/core/manager.py:595-624` 的公共
  build_production 在初始化后无条件 ensure_embeddings。
  `backends/sqlite.py:2004-2033` 先读 lineage：空库/无缺向量时可不 encode；
  有缺失向量或 lineage 变化需重建时，会实际 embed_batch。
  **因此纯 Host lazy wrapper 只解决构造预加载，旧数据补索引仍可能在启动加载。**
- Human v7 builder（同 manager.py:503）初始化数据库和已有向量缓存，没有直接
  调用 embed；v7 `sqlite_v5.py:2298/2909` 在向量 generation 重建或向量查询
  才使用 embedder。Host short_indexing.py:69 调的是文本 projection 重建，
  不能把它等同于向量 generation 重建。已有向量缓存仍有独立内存成本。
- `main.py:4971-4999` 有条件性的 skill prewarm/warmup；当前 WeMM 没有 warmup，
  不要为 lazy 状态兼容新增一个会触发该启动钩子的 warmup。vector_worker 启动
  补索引也有条件，不能把代码存在当作本候选实际运行过的证据。

## 建议最小改法（尚未实施）

1. **先做 Host 延迟权重加载。** 构造只保存已解析本地路径、device 和固定模型
   元数据；连 sentence_transformers import 一并放进首次真实 embed 的加载阶段。
   kind/dim/lineage/status 均不加载模型。维度从已核 pinned snapshot 的轻量元数据
   或受校验的明确契约得到；首次加载核对模型报告维度及输出长度，冲突拒绝，
   不临时修改 lineage。保持 local_files_only/trust_remote_code 的原本地 snapshot
   约束、编码入口、归一化、模型名/revision/fingerprint，不触发无谓重新索引。
2. **加载只允许一个实际 worker。** 共享 load task/future，调用方通过 shield
   等待；请求取消不清掉仍在运行的 task、不重开第二个 SentenceTransformer。
   现有 `async with lock: await to_thread(...)` 在取消后会释放 asyncio 锁，线程
   却未停止，不能直接照搬为加载锁。加载/encode 的物理互斥须持续到线程完成，
   可由独立受管理 task 持锁并加线程锁兜底；取消者不再提交后续 encode、不返回
   迟到结果。失败只在 worker 确认结束后转 failed，下一次明确使用再有界重试。
   不引入后台自动重试风暴，也不把取消解释为内存已释放。
3. **若要求旧库也冷启动无权重，需要一个 SDK 小增量。** 当前公共 builder 没有
   独立的“延迟 catchup”参数。建议增添明确 catchup 时机选项，保留现有生产模型/
   resource 校验和兼容默认；Host 的 HUMAN 空闲入口选择按需，在首次真实向量
   读写前走同一公共 ensure/catchup，尚未完成时如实报 pending/degraded。
   不 monkeypatch ensure_embeddings，不用通用 build 绕生产校验，不传 None/hash，
   不把回填搬到启动后的后台就宣称省掉模型内存。此项需要 SDK owner 明确契约。
4. **状态必须区分 configured 与 loaded。** `backend/p4_ipc.py:375` 对无 is_ready
   的对象默认 True；WeMM 应提供无副作用就绪状态，并补 cold/loading/ready/failed
   映射。仅 is_ready=False 会被 `EmbedderStatusCard.tsx:55` 显示为持续“加载中”；
   该卡 `:88/112` 还硬编码 BGE-M3，应改成真实 WeMM 名称与“按需加载”，保留设置
   的模型选择、资源缺失错误及已配置路径。不能为状态查询触发加载或伪报 ready。

## 后续最小验收（本次未跑）

- 构造、dim/lineage、Settings、图谱和审计读取：模型构造次数 0；分别覆盖空库、
  已完整索引库，以及缺向量旧库（后者须显式验证 SDK catchup 方案）。
- 并发首请求仅加载一次；首 waiter 取消后第二请求不重复加载/并发 encode；
  加载失败、重试和关闭期间状态真实，无悬空 task 警告、无取消后结果发布。
- 固定 2048/L2/lineage 字段逐项保持，错误模型维度被拒；首次真实向量请求仍使用
  用户 WeMM，而非关闭能力或替换模型。首请求超时与既有 SDK deadline/degradation
  需明确：冷加载可能超预算，不能悄悄放宽原 deadline。

建议先交付 Host wrapper＋状态小片并诚实保留旧库 catchup 限制，再补 SDK 公共时机
选项。收益是**首次实际需要 embedding 之前**不加载权重；首次使用后的约 6GB 常驻
不会因 lazy 自动消失，空闲卸载/跨进程共享属于另一项资源生命周期工作。
