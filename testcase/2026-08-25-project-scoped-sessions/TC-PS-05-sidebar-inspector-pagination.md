---
id: TC-PS-05
purpose: Verify grouped sidebar, read-only inspector, same-project creation, pagination consistency, and concurrent catalog mutation
status: active
surface: desktop-ui
type: hybrid
obligations:
  - TO-A5
  - TO-R3
tags:
  - sidebar
  - inspector
  - pagination
entrypoint: session sidebar
revision: 1
---

# TC-PS-05 — 项目化侧栏、Inspector 与分页一致性

## 前置

- 小型 UI fixture：两个 Projects，每个至少两个可区分 Sessions，另有两个无项目 Sessions。
- 大型列表 fixture：500 Projects × 200 Sessions，包含稳定、唯一标题；允许分页或虚拟化。

## 步骤与预期

| 步骤 | 操作 | 预期结果 |
|---:|---|---|
| 1 | 在当前实际桌面构建展开侧栏并切换两个 Project 与无项目区域中的 Sessions。 | Sessions 按 Project 分组且无误归；无项目会话独立成组；切换后消息只属于所选 Session。 |
| 2 | 在一个 Project 点击“新建同项目 Session”。 | 空 Session 立即出现在同一 Project 下并被选中；其他组不新增条目。 |
| 3 | 查看右侧固定区域，在宽屏和窄屏各检查一次。 | 始终可见项目名、`project_root`、`execution_root` 和可用 Git 状态；有复制路径、打开目录、新建同项目 Session；没有修改根目录入口。 |
| 4 | 真点击复制路径与打开目录。 | 剪贴板内容等于显示路径；系统 Finder/Explorer 打开对应目录，不打开其他 Project。 |
| 5 | 加载大型列表，从首屏连续翻页/滚动，记录每页边界；期间在另一个客户端或允许的公开 fixture 入口新增/删除/重命名 Session 后继续。 | 响应在 500×200 fixture 上 p95 ≤200 ms；旧游标不会静默返回混合快照，客户端明确刷新或得到一致下一页；最终无重复、遗漏或误归组。 |
| 6 | 重复刷新、切换组和回到首屏。 | 排序稳定；空 Session、刚创建 Session 与既有历史 Session 均不消失或重复。 |

## 通过条件与证据

- 步骤 1～6 全部满足；步骤 1～4 必须真人 UI，步骤 5 必须保存分页响应摘要、catalog revision 与延迟原始样本。
- 原始截图、录屏、剪贴板核对、性能样本与运行日志写入 `.local-test-evidence`。
