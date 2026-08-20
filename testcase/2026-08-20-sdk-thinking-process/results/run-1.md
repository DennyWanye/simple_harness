# SDK 公开思考过程与终态折叠 — Run 1 结论

- 日期：2026-08-20
- Provider / model：用户配置的 DeepSeek / `deepseek-v4-flash`
- Session：`1a5883b0-b50f-41fb-ada1-50f8811dca2e`
- 补充公开叙述 Session：`81c61acc-2fc4-4410-bc25-47bc33f2205b`
- 最终代码公开叙述 Session：`96ae3cec-e785-486a-9152-78319a569872`
- 结论：目标行为与关键相邻链路 PASS；全新 profile 配置后“不额外重启”的独立 cold 场景未重复执行。

## 自动化

- backend 相关：87 passed（delivery、provider、catalog、memory、public projection、execute）。
- frontend 全量：73 files / 600 passed。
- TypeScript typecheck、debug Tauri build、`git diff --check`：PASS。

## Computer Use

- 运行态：5 秒时思考分组默认展开；`memory_recall`、`memory_search`、`read_file×2` 均成功。
- 终态：19 秒时自动折叠，最终回复独立；点击可展开和再次收起。
- 冷恢复：首次复测发现耗时 19 秒重启后缩为 10 秒；修复后同一历史 Run 恢复为 19 秒，默认折叠，
  展开后四组工具仍全部 `ok`。
- 公开叙述补测：真实 DeepSeek tool-only Run 的每次工具调用前显示有界工作叙述；13 秒终态自动
  折叠，点击可复看；完整重启后默认折叠，展开后叙述与失败/成功工具状态均完整恢复。
- 最终代码复测：运行 5 秒时已展开并出现公开叙述，15 秒终态自动折叠，点击后重新显示全部叙述、
  工具调用和成功/失败结果。
- 操作方式：Computer Use AX/坐标真实点击与输入；未使用 DOM 或 WebSocket 注入。

## 本地证据索引（Git ignored）

根：`.local-test-evidence/2026-08-20/sdk-thinking-process-run-1/`

| 文件 | SHA-256 |
|---|---|
| `cu-final-running-expanded-pass.jpeg` | `a1eaf91b705c365c0e34ee7908decc1c6c40fab8cfa331cb95be278224c58380` |
| `cu-final-terminal-collapsed-pass.jpeg` | `d83f4dd034ba31b35cdd826dd079e84cd8d6aa90111b6aa0ca58ce2f6529e0a0` |
| `cu-final-manual-expanded-pass.jpeg` | `d43b741107136e538e3cf9ad7f647a798ef377c44831c4f54121161b47960a3a` |
| `cu-final-manual-recollapsed-pass.jpeg` | `1bd09317cc305e2f86c7263ffcde3db4df3c257e21e49d153b610ad87ace3289` |
| `cu-final-restart-expanded-pass.jpeg` | `b78ea96ecfcc2ac94775db2417a43afe97ca5068d829117148f0d1bf5b8af07d` |
| `cu-public-narration-pass.jpeg` | `6f9d4440341ac557a924f8281bb99da4f4075833f9d74eb23afdba1b8ce321ed` |
| `cu-public-narration-restart-pass.jpeg` | `55f115a9c523a002572baee7b0683cf2d476c7afc8d4094af0e681948eb59250` |
| `cu-public-narration-final-code-running-pass.jpeg` | `8f6e89a98d5c60378e4f915aa005ccc78cc424f0d3141119afbc1eb70cb82b44` |
| `cu-public-narration-final-code-collapsed-pass.jpeg` | `97bc876fa28645f1f71a35fe400cb93fdaf8c26c8f0e1cab9253ac8726ee5568` |
| `cu-public-narration-final-code-expanded-pass.jpeg` | `1aa48812607f4f93f425369895b59fe1345e247d1ee7bb5350e848c5d740ecb7` |
