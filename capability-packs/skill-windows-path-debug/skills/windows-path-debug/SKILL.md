---
name: windows-path-debug
allowed-tools: []
description: Windows path debugging background knowledge
triggers: [Windows 路径, windows path, 反斜杠, backslash, 盘符路径]
user-invocable: false
---

Windows 路径调试要点：优先使用绝对路径；命令行参数里含空格时必须加引号；
在 Python 字符串中用原始字符串或双反斜杠；跨平台拼路径优先用 pathlib.Path。
