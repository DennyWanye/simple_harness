# Frozen 后端 BGE-M3 embedder 静默降级 mock —— 根因与修复

**日期**：2026-06-28
**症状**：PyInstaller frozen 后端（`F:\deskpet-build\dist\deskpet-backend\deskpet-backend.exe`）
启动时 BGE-M3 embedder worker 加载失败 → 静默降级 mock embedder → 记忆/向量召回退化。
**影响面**：所有历史发布版（≥ beta.4）都中招——frozen 下 BGE-M3 从未真正工作过。
**与空 Bearer 崩溃无关**，是独立的预存问题。

---

## 架构回顾

embedder 用**子进程 worker** 隔离 PyTorch（避免和 ctranslate2 共存 segfault）：
`embedder.py::_spawn_subprocess_worker` 通过
`asyncio.create_subprocess_exec(sys.executable, "-X","utf8", "-m",
"deskpet.memory.embedder_worker", ...)` 拉子进程。

frozen 下 `sys.executable = deskpet-backend.exe`，PyInstaller bootloader **总是跑
main.py**，由 `main.py:17 dispatch_frozen_worker_if_requested()` 识别
`-m deskpet.memory.embedder_worker` 并转发到 worker 的 `main()`。所以 worker 子进程
**共享父进程的 PYZ / sys.path**——`embedder.py` 里那段往子进程注入
`PYTHONPATH=<backend_root>` 的逻辑在 frozen 下是无效摆设（backend 源码目录根本不是
frozen 代码所在地）。真正决定能否 import 的是**bundle 里有没有这个模块**。

---

## 根因：`from FlagEmbedding import BGEM3FlagModel` 在 frozen 下连撞三层

在**真二进制**上把 frozen exe 当 worker 直接跑、抓 worker 的 fatal envelope（含完整
traceback）逐层定位（不是脚本回放，是真实运行栈）：

### 坑 1 —— `No module named 'datasets'`（hard import，非 soft check）

```
embedder_worker._load_model → from FlagEmbedding import BGEM3FlagModel
  → inference/embedder/encoder_only/m3.py:13   ← 推理模块 import 了 finetune 模块
    → finetune/embedder/encoder_only/m3 → abc/finetune/embedder/AbsDataset.py:5
      → import datasets        ← 裸 import，FlagEmbedding 1.3.5 的烂分层
ModuleNotFoundError: No module named 'datasets'
```

- `datasets` 纯属**训练**依赖，推理一行都不调（唯一的模块加载期引用是
  `AbsDataset._get_file_batch_size` 的函数注解 `temp_dataset: datasets.Dataset`，
  且该文件**没有** `from __future__ import annotations` → 注解在定义期被求值）。
- spec 里 `excludes=["datasets"]` 是**对的**——bundle 真 datasets 会拖
  pyarrow/pandas/dill/multiprocess/xxhash ~150MB，且历史上让 PyInstaller 构建期
  依赖分析崩 `SubprocessDiedError`。
- **误区**：spec 旧 NOTE 说"推理 `is_datasets_available()` 可选、exclude 安全"。
  那个 transformers soft check（`find_spec(...) is not None`）确实安全，但真正崩的是
  **FlagEmbedding 自己** AbsDataset 里的裸 `import datasets`，跟 transformers 无关。
- 一开始 source venv 复现失败是因为模拟探针让 `find_spec` 返回 None 后，import 机制
  **回退到下一个 finder**，把 site-packages 里物理存在的真 datasets 加载了——假阴性。

### 坑 2 —— `OSError: could not get source code`（inspect.getsource）

datasets 过了之后暴露下一层：`FlagEmbedding/__init__.py` eager import 了**整个
reranker 全家桶**（我们只要 embedder），reranker MiniCPM modeling 在 class 定义期用
transformers 的 docstring 装饰器 → `utils/doc.py:get_docstring_indentation_level`
对*方法*调 `inspect.getsource`，frozen 下没有 .py 源码 → `OSError`。

### 坑 3 —— `No module named 'transformers.models.metaclip_2'`（动态枚举）

tokenizer 过了之后构建 `BGEM3FlagModel` 时：`tokenizer_class_from_name` 按
`TOKENIZER_MAPPING_NAMES` 动态 `import_module` 各模型子包。BGE-M3 的
`XLMRobertaTokenizerFast` 也挂在 MetaCLIP-2 名下，遍历到字母序靠前的 `metaclip_2`
时 frozen 没打包该子模块 → `ModuleNotFoundError`。真正要的 `xlm_roberta` 在后面。

> 三层逐个揭开后 worker 终于 `ready` (is_mock=False) 且真 encode 出归一化 1024 维
> 向量，**无第四层**。

---

## 修复（单点，零体积，零 spec 改动）

全部三层在 `deskpet/memory/embedder_worker.py` 的 `_apply_frozen_compat()` 解决，
**仅 frozen 生效**（`sys.frozen` 守卫，dev/source 模式三坑都不存在 → no-op），在
`_load_model` 里 `import FlagEmbedding` **之前**调用：

1. `_install_datasets_stub()` —— 往 `sys.modules` 注入一个只含 `Dataset` 的轻量
   `datasets` stub（`sys.modules` 优先级最高、确定性 Python 语义）。训练期符号被真
   用到才 `RuntimeError`。
2. `_patch_transformers_frozen_quirks()`：
   - 坑 2：包一层 `get_docstring_indentation_level`，`getsource` 失败时回退固定缩进。
   - 坑 3：包一层 `tokenizer_class_from_name`，对缺失的无关模型子模块 `continue` 跳过。

**spec 不动**：datasets 继续 `excludes`，**不要**加 `collect_submodules("datasets")`
（无用且拖体积）。spec 里的 NOTE 已更新为 RESOLVED 并指向本修复。

### 改动文件
- `backend/deskpet/memory/embedder_worker.py` —— 新增 `_apply_frozen_compat` 等 3 函数 + `_load_model` 调用
- `backend/deskpet-backend.spec` —— 更新 datasets exclude 上方的 NOTE（RESOLVED）
- `backend/tests/test_frozen_embedder_compat.py` —— 新增单测（stub 行为 + frozen 守卫 + 调用顺序）

---

## 验收

- **单测**：`pytest tests/test_frozen_embedder_compat.py tests/test_frozen_worker_dispatch.py
  tests/test_deskpet_embedder.py` → 21 passed。
- **真 frozen 产物**（决定性，项目纪律）：重打包后 `scripts/probe-embedder.ps1` 跑真
  `deskpet-backend.exe`，期望日志出现 `BGE-M3 subprocess worker ready` 且最终
  `is_mock=False`、无 `No module named`。
  - 见 `01-acceptance-evidence.md`。
