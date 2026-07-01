# 013 · 桌宠隐藏后无法直接从桌面唤回

- **状态**：resolved
- **严重度**：medium
- **发现日期**：2026-06-16
- **关联需求**：
  - [019-pet-actionbar-rework-and-settings-shell](../../requirements/019-pet-actionbar-rework-and-settings-shell/)（隐藏入口落地需求）
  - [041-pet-edge-recall-interaction](../../requirements/041-pet-edge-recall-interaction/)（修复指向）

## 现象

019 在桌宠 ActionBar 中新增了"隐藏桌宠"按钮（icon `EyeOff`，invoke `hide_pet`），点击后整个 pet 窗 `hide()`、形象与 ActionBar 一并消失。当前**唯一可见的唤回路径** = 系统托盘菜单 "显示/隐藏桌宠"（`toggle_pet`）。

桌面端用户视角的体感缺口：

- 隐藏后桌面上没有任何可见的"桌宠还在、可以喊回来"的痕迹
- 用户必须知道"系统托盘那个 🐾 图标"才能找回 —— 对不熟悉桌宠类应用的用户存在发现性问题
- 调研里所有同类桌宠（clawd-on-desk / TinyRoommate / Codex Pet / NekoAI 等）都有"从桌面直接唤回"的方式（边角驻留 / 全局热键 / 屏幕角落小图标等），是桌宠标准交互的一部分

## 根因初判

非 bug，是产品交互未设计完整。019 的 declare 阶段确认本期只做"隐藏入口"+"沿用托盘唤回"，**显式不引入新的桌面唤回机制**（避免与 ActionBar 容器层重做的 declare 互相干扰），把这条线索登记到 issues / 后续独立立项。

## 影响与判断

- **影响**：用户隐藏桌宠后存在"找不回来"的发现性风险（中等严重——托盘菜单仍可用、不是数据丢失/不可恢复）
- **不阻塞**：019 全部 AC（AC-1 ~ AC-10）；不阻塞 015 / 016 / 017a / 017b 任一既有 AC
- **判断**：优先级 medium —— 隐藏功能本身工作正常、托盘唤回路径仍然 first-class；唤回交互本身是独立产品决策（涉及屏幕边角驻留 / 全局热键 / 托盘改造等多种方案），不该塞进 019 这种 UI 容器层重做需求里

## 可选修复方向

候选交互方案（不锁定，留给后续立项时调研）：

1. **屏幕边角"小尾巴"驻留**：桌宠 hide 时不彻底 hide，缩成屏幕某条边的一小角（如 24×8 像素的小色块或微缩 icon），用户点击/hover 唤回。优点：永远可见、不依赖外部菜单。缺点：占屏幕空间、需要选择"哪条边"的策略
2. **全局热键唤起**：通过 Tauri Global Shortcut plugin 注册一个全局热键（如 `Cmd+Shift+P` / `Ctrl+Shift+P`），任何时候按下都唤回桌宠。优点：不占屏幕。缺点：发现性差（必须文档告知）、热键冲突可能性
3. **点托盘图标直接唤起（不进菜单）**：当前 `show_menu_on_left_click(true)`，左键就出菜单；改为左键直接 toggle_pet，右键才出菜单。优点：极简、复用现成基建。缺点：可能与"打开对话"等其他常用菜单项冲突（用户更想点对话时被迫先 toggle_pet）
4. **屏幕角落驻留小图标**：右下角永远显示一个 24×24 的桌宠图标（独立窗口或集成到托盘扩展），点击唤回。优点：发现性强。缺点：实现成本最高（新窗口 / 跨平台一致性）

实施期立项时建议先做**调研 spike**：调研 4 种方案的桌宠类项目惯例 + 跨平台能力差异，再决策。

## 备忘

- 041 已将 ActionBar 主路径从 `hide_pet` 完全隐藏改为 `petDisplayMode=edgeRecall` 边缘召回；桌面上保留缩小形象作为可见召回点，托盘菜单继续作为完全隐藏/显示兜底。
- 019 实施期在 `frontend/src-tauri/src/lib.rs::hide_pet` 函数注释中已指向本 issue（注释中写"issue 013"）
- 与 [`docs/explorations/desktop-completeness/`](../../explorations/desktop-completeness/) Tier 2 "右键菜单 + 双击反应" 部分相关：右键菜单 / 双击反应中也可能承载唤回交互，立项时一并考虑
