在 DeskPet 项目实现 PPT Pro 计划的 image_tools 部分（WI-5 + WI-6b）。只改 `G:\projects\deskpet\backend\deskpet\tools\image_tools.py` 一个文件，别动别的。

先读权威计划：`G:\projects\deskpet\plans\2026-06-21-ppt-deepresearch-pro\00-PLAN.md` 的 WI-5、WI-6b 段（含 §2.2 回退判定）。

要实现：
1. **WI-5 `probe_image_reachable(*, timeout_s: float = 8.0) -> bool`**（新增模块级函数）：便宜探测 relay images 服务可达性——`GET <base>/v1/models`，`trust_env` 用现有 image 配置（grep 现有 `_generate_png` 里 base url / key / trust_env 的解析方式，复用同一来源；若没有独立 helper，抽一个 `_resolve_relay_base_and_key() -> tuple[str|None, str|None]` 内部函数供 probe 与 `_generate_png` 共用，避免漂移）。connect 5s / read timeout_s。返回 `r.status_code < 500`；base 缺失或任何异常 → False（log.info 记一行）。**从不抛异常**。

2. **WI-6b 结构化失败分类**：给 `_generate_png` 与 `generate_images` 的**失败返回加 `error_kind` 字段**（不破坏现有 `error` 字段与现有调用方，纯加键 = 向后兼容）。
   - `_generate_png` 现签名返回 `(bytes|None, err|None)`。改为返回 `(bytes|None, err|None, error_kind|None)` 三元组**会破坏调用方**——所以**不要改签名**；改为：保持 `(png, err)` 二元返回，但**另维护一个分类逻辑**，让 `generate_images` 返回的 dict 里带 `error_kind`。具体：把失败分类抽成 `_classify_image_error(status_code: int|None, body: dict|str|None, exc: Exception|None) -> str`，在 `generate_images` 组装结果 dict 时调用它填 `error_kind`。`_generate_png` 内部可顺带把分类信息透传出来（例如返回 `(png, err)` 不变，但在 generate_images 层根据捕获的异常/status 重新分类）——你自行选最小改动方式，关键是 `generate_images` 每个失败项的 dict 含 `error_kind`，且现有 `{prompt, path, error}` 字段不变。
   - 分类规则（**分层、优先级从高到低**）：
     a) HTTP status_code 最可靠：connect/read 超时、RemoteProtocolError、ConnectError、ReadError、WriteError、ProtocolError、SSLError、502、503、504 → `"connectivity"`；401/403 → `"auth"`；429 → `"quota"`。
     b) 解析返回体结构化 `error.code`/`error.type`/`code`（若有）：含 `model_not_found`/`model`+`unsupport`/`invalid_model` → `"model_unavailable"`；`content_policy`/`safety` → `"content"`。
     c) 多语言文案兜底（relay 只给自然语言 message 时）：message 含 `model` 且含 (`not found`/`unsupport`/`不支持`/`不存在`/`无可用`) → `"model_unavailable"`。**这层依赖文案、可能漏判，写注释诚实标注，由上层「全图失败也回退」兜底。**
     d) 其余 4xx → `"content"`；无法归类 → `"unknown"`。

约束：
- 中文注释；保持文件现有风格与超时/重试逻辑（`_MAX_ATTEMPTS`/`_RETRY_BACKOFF`/读超时不重试防双倍扣费等）不破坏。
- 不要动除 image_tools.py 以外任何文件。
- 实现后跑 `cd /g/projects/deskpet/backend && .venv/Scripts/python.exe -m pytest tests/ -k image -q` 确认现有图像测试不破（若有失败，修到绿）。
- 自己补 2-3 个针对 `probe_image_reachable` 与 `_classify_image_error` 的单测（mock httpx / 构造各类 status+body），放到现有 image 测试文件或新建 `tests/test_image_probe_classify.py`。

验收：probe 函数可用 + generate_images 失败项带正确 error_kind（connectivity/model_unavailable/auth/quota/content/unknown）+ 现有 image 测试全绿 + 新单测绿。完成后简述你改了哪些函数、新单测覆盖了什么。