---
name: godot
description: 创建、检查和修复 Godot 4 游戏引擎项目、2D/3D 游戏 Demo、场景、角色移动、跳跃、敌人 AI 和 GDScript；使用真实 Godot CLI/编辑器验证，不依赖预制游戏模板。
when_to_use: 用户提到 Godot（包括常见误拼 Gobot）、游戏引擎、project.godot、GDScript，或要求创建、运行、检查、修复带场景和角色玩法的 2D/3D 游戏 Demo 时。
version: 1.0.1
author: deskpet
task_types: [task]
requires_script: false
---

# Godot 4 项目执行指南

本能力提供 Godot 特有的知识、环境探测和真实 headless 检查。文件编辑、下载、进程启动、
窗口操作和截图必须继续使用 DeskPet 的通用工具；不要在能力包里复制这些原语。

## 0. 先确认用户指的是哪个技术

- 在“游戏 Demo、角色移动、跳跃、敌人 AI、场景”等游戏开发语境里，`Gobot` 很可能是
  `Godot` 的误拼；必须优先按 Godot 候选做能力检索或向用户确认。
- 不要因为看见 `Gobot` 就擅自切换到 Go 语言机器人框架，也不要先运行 `go version`。
- 如果上下文仍然同时支持两种解释，先用一句简短问题确认，再执行会写盘或安装依赖的动作。

## 1. 先接地环境事实

1. 调用 `godot__detect`，记录真实 executable、版本和来源。
2. 只使用 Godot 4.x。检测到 Godot 3.x 时不要勉强运行项目。
3. 缺少 Godot 时，先让通用下载/安装能力选择 Godot 官方发布页或可信的当前用户级、
   portable 安装，再校验来源与发布哈希，随后重新调用 `godot__detect`。
4. 不要声称“已安装”，除非探测结果包含可执行路径且 `--version` 成功。

## 2. Godot 4 项目约定

- 项目根必须包含 `project.godot`。
- 场景使用 `.tscn`，脚本使用 GDScript 2 (`.gd`)；资源路径使用 `res://`。
- 主场景在 `[application] run/main_scene` 中显式声明。
- 节点引用优先使用 Godot 4 的 `%UniqueName`、`@onready` 或导出 `NodePath`，不要依赖脆弱
  的绝对树路径。
- 信号连接、输入动作和 autoload 必须在代码或 `project.godot` 中可追踪。
- 用户项目的可复用逻辑按职责拆分；不要把完整游戏塞进单个 `_process()`。

## 3. 验证顺序

每轮项目变更后：

1. 先检查 `project.godot`、主场景和脚本引用是否存在。
2. 调用 `godot__project_check(project_path=...)`。它会运行真实
   `godot --headless --editor --path <project> --quit`。
3. 若检查失败，根据 `diagnostics` 修用户项目；项目脚本/场景错误不是能力包错误，不能
   因此派生 Godot 能力包版本。
4. headless 通过后，用通用进程原语启动编辑器或项目，并用通用桌面/截图能力检查实际画面。
5. 只有同时具备 CLI 结果和 GUI/画面证据时，才能声称项目可运行。

## 4. 常见 Godot 4 修复

- `Parse Error` / `Parser Error`：按报错文件和行号修 GDScript 2 语法，不要切回 Godot 3 API。
- `Failed loading resource`：检查 `res://` 路径、大小写、外部资源 ID 与被移动文件。
- 主场景缺失：创建或修复 `.tscn` 后更新 `run/main_scene`。
- 输入无响应：核对 `[input]` actions 与代码中的 action 名完全一致。
- `Node not found`：检查场景实例层级；优先改为唯一节点名或显式导出引用。
- headless 通过但窗口异常：继续用真实编辑器/运行窗口、日志和截图诊断，不把 headless
  检查当成视觉验收。

## 5. 边界

- 本能力不提供任何预制游戏、场景或塔防模板。
- 不自行下载或启动应用；使用通用受权工具。
- 不绕过 Windows UAC、登录、验证码或系统对话框。
- 不把用户项目错误归类为能力实现错误；只有 adapter 自身协议、探测或命令构造错误才进入
  capability repair。
