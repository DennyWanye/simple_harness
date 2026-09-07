# 图谱密集标签改动独立审查（提交 d3a2fdf4）

审查日期：2026-09-07。审查对象：`git show d3a2fdf4 -- tauri-app/` 与 `ARCHITECTURE/UI.md` 对应段落。只读审查，未改任何仓库文件、未运行原生应用。

## 结论：ACCEPT

改动正确、范围收敛、有真实 headless Cytoscape（`styleEnabled: true`）测试覆盖，文档与实现一致。下面列出的都是非阻塞建议，可随后续 r8 原生截图验证一并处理，不要求在本提交修改。

## 验证记录（本机实测）

| 项目 | 结果 |
|---|---|
| `npx vitest run src/components/MemoryGraphCanvas.test.ts` | 2 通过、1 跳过（真实 fixture 需 `HOST_GRAPH_FIXTURE`） |
| `npx vitest run src/components/PrimaryMemoryGraph.test.tsx` | 通过 |
| `npx tsc -b --noEmit` | 无错误 |
| `npx eslint` 四个改动文件 | 无告警 |
| 临时探针（45 节点、含 contested 节点与 128 字边标签，已删除） | 见下文各条 |

探针实测计算样式：密集态基础 `font-size 10px / text-background-opacity 0.85 / z-index 0 / width 32px`；选中后 `font-size 12px / opacity 1 / z-index 10 / border-color 白 / text-max-width 200px / label 140 字`；contested 节点选中后边框由橙变白（与旧 `:selected` 行为一致）；少量态基础 `12px / opacity 0 / padding 2px / width 44px`。

## 1. 分级阈值与密集态样式

- 阈值 ≤12→70、≤40→32、>40→18 与画布尺寸（高 `min(340px, 40vh)`、无边时网格 `ceil(sqrt(n))` 列）匹配：50 节点为 8 列，每格约 32px 节点 + 8px 边距 + 96px 标签 ≈ 140px，18 字在 10px 字号、96px 宽下约折 2 行，整图 fit 后不会被标签撑成一大片文字。
- 密集态 32px 节点、10 字号、标签底色 0.85：底色与画布同色（`#111827`），作用是遮住穿过标签的边线，不引入新颜色；0.85 保留少许透明便于察觉被遮的线。合理。
- 取舍结论：**保留当前三档硬阈值，不改为连续公式。** 连续公式（例如按 `sqrt(n)` 缩放预算）只会在 12/40 边界附近少一次跳变，但代价是标签长度随筛选每变一个数字都在变、无法在文档里一句话说清；本改动本来就在 `[nodes, edges]` 变化时整图重建重排，跳变不是新增成本。三档默认值不需要再调。

## 2. 选择器与 Cytoscape 3.34.2 行为

- `node:selected` 用 `data(full_label)`：Cytoscape 样式按声明顺序覆盖（源码 `applyContextStyle` 逐条上下文覆盖，无 CSS 特异度），`node:selected` 位于所有 `node[...]` 规则之后，所以覆盖 `label`、`font-size`、`text-max-width`、`border-*` 均生效；contested 节点选中后边框变白，与旧 `:selected` 一致。旧 `:selected` 同时给节点写 `line-color/target-arrow-color`（对节点无效），拆成 `node:selected` / `edge:selected` 后语义更准确，无行为差异。
- 非密集态 `text-background-opacity: 0`：Cytoscape 默认值本来就是 0（`'text-background-opacity': 0`），无视觉回归。`text-background-padding: 2px` 会把标签包围盒各边加 2px（`text-background-padding` 参与 label bbox 计算，且本来就有 2px marginOfError），对 `nodeDimensionsIncludeLabels` 布局影响可忽略。
- `z-index`：3.34.2 支持（`z-index-compare: auto` 下先节点后边，再按 `z-index` 排序；`getCachedZSortedEles` 用 `zIndexSort`）。画布渲染 `drawCachedElement` 把节点本体与标签作为同一元素连续绘制，所以选中节点连同其 140 字标签整体压在其它节点与标签之上，达到"密集图里也能读全"的目的。
- 选中后非密集态标签也会得到底色 1（与画布同色），只会遮住底下经过的边线，是改善而非回归。

## 3. 文字列表 / 详情栏与预算跳变

- `PrimaryMemoryGraph.tsx` 的 `<ul aria-label="图中记忆">`、关系列表与 `<aside aria-label="选中记忆详情">` 全部直接使用 `n.label`（服务端上限 512 字），未经过 `canvasLabel`；`graphElements` 只影响 Cytoscape data。完整标签始终可见，与 UI.md 陈述一致。
- 预算按筛选后的 `nodes.length` 计算：筛选把 45 条减到 40 条时标签从 18 字跳到 32 字。可接受——`MemoryGraphCanvas` 本来就在 `[nodes, edges]` 变化时销毁并重建整个实例并重新布局，用户看到的是整图重排，标签长度变化是其中最不显眼的一部分；而且用"当前视图条数"而不是"全库条数"决定预算是正确的，筛选后节点少了本来就该显示更长标签。

## 4. 测试覆盖

- 真实覆盖：测试用 `cytoscape({ headless: true, styleEnabled: true, style: graphStyle(...) })`，通过 `node.style("label")` 读取计算样式，选中前后分别断言等于截断标签与完整标签，这是对样式表而非对纯函数的验证，覆盖了本改动的核心承诺。
- 漏测（非阻塞）：
  - `full_label` 的 140 字上限没有断言（测试用的长标签不到 140 字）；探针实测 160+ 字标签 `full_label` 长度为 140，实现正确，建议补一行 `expect(Array.from(node.data("full_label")).length).toBe(140)`。
  - 少量态断言 `expect(...).toBe(many[0].label.length <= 70 ? many[0].label : ...)` 用实现公式推算期望值，实际只走了"未截断"分支；建议直接写 `toBe(many[0].label)`，意图更清楚。
  - 边标签未测，但本提交对边只改了密集态字号（11→9），且服务端解析要求 `e.label === e.relation_kind`（≤128 字的关系类型名），不存在长边标签问题，不需要预算。
  - contested + selected 的边框覆盖、`z-index` 生效均未断言（此前也没有），探针已实测通过，可选补充。

## 5. 文档一致性

`ARCHITECTURE/UI.md` "2026-09-07 图谱密集标签可读性" 段落的阈值、样式、`full_label` 140 上限、`graphStyle(nodes.length)` 调用点、测试方式、"vitest/typecheck/eslint 通过"均与代码和本机实测一致；明确标注真实 WebKit 密集画布截图待 r8 一并做，没有过度声明。

## 非阻塞建议汇总（可随 r8 一起做）

1. 测试补 `full_label` 140 字上限断言；少量态断言改为字面期望值。
2. r8 真实 WebKit 截图时重点看 50 节点无边网格在窄窗口 fit 后的有效字号（预计 ~7–8px），若不可读再考虑把密集态 `text-max-width` 从 96px 降到 80px，而不是调阈值。
