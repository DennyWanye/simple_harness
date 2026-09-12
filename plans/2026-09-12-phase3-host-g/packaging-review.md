# P3.3 G macOS packaging review

更新：2026-09-12。范围：Host frozen backend 路径、SDK 收集、Playwright macOS arm64 契约及直接消费者。

**状态：主任务 scoped production review ACCEPT；源码已冻结。实际 PyInstaller/Tauri 冻结构建尚未完成，安装包验收仍为 PENDING。** 本报告不将源码环境浏览器测试或 frozen-launcher 单测表述为冻结产物运行成功。

## 实现与边界

- `backend/deskpet-backend.spec` 从 `sdk_candidate` 获取并验证当前 Harness/Service wheel 与 manifest，Service 保持 0.3.13；不再硬编码旧 wheel 文件名。收集 `agent_orchestrator` 动态模块与数据，不绕过 execution build manifest 检查。
- macOS resolver 使用 `backend/deskpet-backend`，Windows 继续使用 `.exe`。显式开发环境覆盖和回退保留；隐式编译期源码路径仅 debug 可用。
- Playwright acquisition、spec helper、runtime executable resolver 统一选择本机契约。Windows x64 原 archive/executable pin 与 ownership marker 兼容；新增 macOS arm64。macOS x64、Linux、Windows ARM64 明确报 unsupported，不以 Windows 浏览器代替。
- mac 浏览器树在 PyInstaller Analysis 后按 DATA 收集，避免其 Mach-O 被重新分类、改写及重签名而破坏原字节 pin。实际冻结产物仍须再次核验。
- Node driver 路径统一分隔符；只追踪本池浏览器的 driver 祖先。真实 idle cleanup 测试要求非空 browser/driver 集合及父子关系，并按 PID＋创建时间确认退出。
- base Tauri 配置不依赖 ignored 构建输入。主任务生成 candidate overlay，将 `.local-test-evidence/2026-09-12/p33-g/frozen-dist/deskpet-backend` 的实际绝对路径映射到 `Contents/Resources/backend`，不增加独立浏览器资源树、不影响 Windows overlay。
- spec 保留必要模块、精确 wheel、许可证和运行数据，不复制 SDK checkout/backend 工作树；对展开后的 `.env`、私密凭据、`secrets/` 等数据路径拒绝打包。最终产物泄漏检查仍待构建后执行。

## 官方来源与实际身份

版本来自本机安装的 Playwright **1.61.0** `driver/package/browsers.json`：Chromium Headless Shell **r1228 / 149.0.7827.55**。`driver/package/lib/coreBundle.js` 的 `cftUrl` 与 mac-arm64 映射给出实际下载地址；另核对了 [Playwright 官方浏览器说明](https://playwright.dev/python/docs/browsers)。

实际下载：[官方 CDN mac-arm64 归档](https://cdn.playwright.dev/builds/cft/149.0.7827.55/mac-arm64/chrome-headless-shell-mac-arm64.zip)。下载使用 HTTPS、200 MB 上限、240 秒时限，文件保存在 ignored G evidence 下。以下值均由实际归档测得后写入契约，非推测：

| 对象 | 实测身份 |
| --- | --- |
| Archive | `chrome-headless-shell-mac-arm64-149.0.7827.55.zip`；98,043,456 bytes；17 entries；CRC 通过 |
| Archive SHA-256 | `302F82603BE06683947594ECD60F849E362A8FE3DD82A89BD4408477C97E75A6` |
| Archive MD5 | `9EA0A6D16E46DCC685D462D210D78116` |
| Executable | `chrome-headless-shell-mac-arm64/chrome-headless-shell`；159,293,248 bytes；Mach-O arm64 |
| Executable SHA-256 | `11E393326C7D20A7C56641A7C65DEF33EA9C280DA3B0B74CF8563B07989A0EE3` |
| `LICENSE.headless_shell` SHA-256 | `EA614F3494514366B3EE83DB6E3E6DED39E0060C9FF3FB283FFB9A2F60CE59C5` |
| `browsers.json` SHA-256 | `EE39BC924BC3D1BD895626C2910F1292D109BBFEEB5ABD113ACB45E1951CC942` |

离线 acquisition 已真实验证归档、发布 ownership markers、恢复主程序执行位；`file` 确认 arm64，`codesign --verify --strict` 确认官方主程序在磁盘上签名有效。此项不代表经过 PyInstaller/Tauri 后仍保留相同字节。

## 验证记录

| 验证范围 | 结果与来源 |
| --- | --- |
| Backend resolver 定向 Rust | 本 worker：修复前 10 passed / 3 failed；最终 debug **14 passed**、关闭 debug assertions **14 passed**。两次均注入测试用编译期 root；仅 `rustc --test` 单文件 wrapper，无 Cargo/Tauri 构建。 |
| Platform + 全 renderer（真实 mac owner）+ frozen launcher | **主任务报告：59 passed，9.86s**；包含真实 crash/restart 与加强后的非空 driver ownership/idle cleanup。不是本 worker 独立重跑。 |
| Scoped production review | **主任务报告 ACCEPT**，包含 driver matcher 修复。 |
| 静态检查 | 本 worker：相关 Python/spec AST、JSON、diff whitespace 检查通过。 |
| 最终 PyInstaller/Tauri artifact | **PENDING：尚无实际冻结构建完成证据。** |

主任务验证范围的复跑入口（从 Host `backend/`；不要与其他 pytest 并跑）：

```sh
DESKPET_TEST_PLAYWRIGHT_OWNER=/Users/denny/projects/simple_harness/.local-test-evidence/2026-09-12/p33-g/playwright-mac/playwright-browsers .venv/bin/python -m pytest tests/test_playwright_bundle_platform.py tests/test_playwright_renderer_pool.py tests/orchestration/test_frozen_orchestrator_launcher.py -q
```

原始证据仅留本机 ignored 路径，不提交日志、二进制或归档：

- `.local-test-evidence/2026-09-12/p33-g/playwright-mac/archive-inspection.json`
- `.local-test-evidence/2026-09-12/p33-g/playwright-mac/downloads/`
- `.local-test-evidence/2026-09-12/p33-g/playwright-mac/playwright-browsers/`
- `.local-test-evidence/2026-09-12/p33-g/packaging-static/`：Rust wrapper 与测试可执行文件。

## 剩余构建门槛

由主任务完成当前 SDK candidate pin、execution manifest 更新及实际 PyInstaller/Tauri 构建。之后必须在最终 `Resources/backend/_internal` 树再次验证 browser ownership、架构、许可证及上述 executable SHA-256；任何构建/签名步骤改写字节均不得忽略。再确认实际 bundled SDK imports、资源映射、无私密工作树数据，以及由 Tauri 启动 frozen backend 的真实运行结果。完成前不宣称 native G packaging PASS。

本次未提交 Git；生产源码冻结，本文只记录文字结论与证据索引。
