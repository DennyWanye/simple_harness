# TC-WB-09 — 桌宠代码全删（脚本可判）

> 对应 AC：WB-9 ｜ 行为契约：B11
> manual_required: false（纯脚本判定；在仓库根目录执行）

| 步骤 | 命令 | 判定（过/不过） |
|---|---|---|
| 1 | `grep -riE "petcanvas\|pet-anim\|pet-engine" tauri-app/src` | **过**：零命中，或命中行全部是注释（`//`、`/* */`、`{/* */}`、`#`）。任一非注释命中 = 不过。 |
| 2 | `grep -riE "petcharacter\|pettransform\|petmodels\|petstatemachine" tauri-app/src` | 同上判定：零非注释命中。 |
| 3 | `for d in tauri-app/src/pet-anim tauri-app/src/pet-engine tauri-app/src/pet-state tauri-app/public/assets/pet; do [ -e "$d" ] && echo "EXISTS: $d"; done; true` | **过**：无任何 `EXISTS:` 输出（四个目录全部删除）。 |
| 4 | `for f in tauri-app/src/components/PetCanvas.tsx tauri-app/src/components/petCharacter.ts tauri-app/src/components/petTransform.ts tauri-app/src-tauri/src/click_through.rs; do [ -e "$f" ] && echo "EXISTS: $f"; done; true` | **过**：无任何 `EXISTS:` 输出。 |
| 5 | `grep -rn "click_through" tauri-app/src-tauri/src` | **过**：零命中（mod 声明与注册一并移除）。 |
| 6 | `grep -rniE "petmodels" tauri-app/src` | **过**：零命中（含 SettingsPanel 类型引用与测试引用）。 |
| 7 | 残留 import 兜底：`cd tauri-app && npm run typecheck`（= `tsc -b --noEmit`） | **过**：编译零 error（任何指向已删文件的 import 会在此暴露）。 |

判定：步骤 1–7 全部**过**才 PASS。允许例外：CHANGELOG/plans/testcase 等历史文档命中不计（命令已限定 `tauri-app/src`、`tauri-app/src-tauri/src` 范围）。
路径备注：命令按仓库结构 `tauri-app/src`（前端）与 `tauri-app/src-tauri`（Rust）书写；若实现落位不同，以"acceptance 原文的 `src` 即前端源码根"等价换算，不得缩小扫描面。
