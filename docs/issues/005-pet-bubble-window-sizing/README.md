# 005 · PetBubble 应改用独立 window，而非加大 pet 主窗

- **状态**：resolved（2026-06-14 由 [需求 016](../../requirements/016-pet-bubble-independent-window/) 落地修复；M16.9 真跑通过 AC-1 / AC-2 / AC-3 / AC-7 / AC-8 / AC-9 / AC-10）
- **严重度**：medium
- **发现日期**：2026-06-13
- **完成日期**：2026-06-14
- **关联需求**：[015-desktop-pet-bubble-and-conversation-owner](../../requirements/015-desktop-pet-bubble-and-conversation-owner/)（产生本 issue 的真跑场景）/ [016-pet-bubble-independent-window](../../requirements/016-pet-bubble-independent-window/)（修复落地）

## 现象

015 主跑发现 pet 主窗（240×320）容不下气泡（设计 280px 宽）—— 气泡左右被 webview 边界裁切（[截图](../../../docs/requirements/015-desktop-pet-bubble-and-conversation-owner/) M15.8 真跑记录）。当前 hot-fix 把 pet 主窗加大到 480×460 让气泡能完整渲染，但这是凑合方案，不是最佳实践。

## 根因初判

015 design.md §6.1 一开始就写 "pet 窗 240×320 + 气泡 max-w-[280px]"，design 阶段没意识到 280 > 240 必然超界。真跑才暴露。

## 影响与判断

当前 hot-fix（加大 pet 主窗）的真问题：

- **浪费桌面占地**：即便透明，window 也在 macOS 占空间，影响 mission control / 拖动其他 app 时的窗口感知
- **长气泡仍可能超界**：expanded 全文超过 460px 高度时仍被裁
- **不能动态适配**：短文本时 480 太空、长文本时不够
- **形象拖拽区与窗口范围错位**：pet 形象 hit-test 还是 160px 圆形，但 window 480 → 拖空白边时语义模糊
- **违反"window 应匹配内容大小"的设计原则**

但本期机制层 AC 仍成立（气泡能呈现 + chat 不显示主动轮），所以**015 暂不修**——属于产品体验层优化、超本期范围。

## 可选修复方向

业界标准做法（Discord 通知 / macOS Notification Center / NekoAI 桌宠等）：

**方案 A · 独立气泡 window**（推荐）

- 单开第二个 `transparent + alwaysOnTop + decorations:false + skipTaskbar:true` 的小 webview window（label="bubble"）
- 按需 `show()` / `hide()` + 动态 `setSize()` 适配文本长度
- 用 `setPosition()` 跟随 pet 主窗位置移动（监听 pet 窗 move event）
- pet 主窗回到 240×320 原始尺寸

**改动范围估算**：
- `tauri.conf.json`：新增 bubble window 配置
- `frontend/src/pages/bubble/` 新增 entry（参考 pet/chat 多入口模式）
- `frontend/src-tauri/src/lib.rs`：bubble window 位置跟随 pet 窗 + cursor passthrough 处理
- `frontend/src/stores/petBubble.ts`：show/hide/setSize 调用 + 跟随 pet 位置逻辑
- 重做约 M15.5 + M15.6 大半工作量

**方案 B · 全屏 transparent overlay**

- pet 窗做成 screen-wide transparent overlay，pet 形象绝对定位、气泡也绝对定位
- 优点：单 window 极简
- 缺点：cursor passthrough 需更精细的 hit-test；alwaysOnTop overlay 在多 app 切换时行为更难控

我倾向 A。

## 备忘

修复时记得回头改：
- `docs/requirements/015-desktop-pet-bubble-and-conversation-owner/design.md` §6.1（更新位置 / 尺寸语义）
- `tauri.conf.json` pet 窗尺寸回归 240×320
- M15.5 store + M15.6 PetBubble 组件接入新 window 控制

---

## 修复指向（2026-06-14 resolved）

由 [需求 016](../../requirements/016-pet-bubble-independent-window/) 按方案 A（独立 bubble window）落地：

- **代码改动**（feature 分支 `feature/016-pet-bubble-independent-window`，commits `111a18e` + `9d5c12b` + M16.9 final）：
  - `frontend/src-tauri/tauri.conf.json` pet 窗回归 240×320 + 新增 bubble window（transparent / alwaysOnTop / decorations:false / skipTaskbar:true）
  - `frontend/src-tauri/capabilities/default.json` bubble 加入 `windows` 列表
  - `frontend/src-tauri/src/bubble_window.rs` 新模块：50ms 轮询 → 16ms tick + `outer_position` 跟随 pet 主窗、`compute_bubble_position` 翻转/clamp 纯函数 + 8 case 单测、show/hide/set_bubble_size invoke 命令、透明窗 workaround（Tauri #1564）
  - `frontend/src-tauri/src/lib.rs` 015 helper `apply_pet_window_level` 重命名/泛化为 `apply_floating_window_level` 供 bubble 复用
  - `frontend/src-tauri/src/push_subscriber.rs` emit_to 目标从 `"pet"` 改为 `"bubble"`（M16.6 漏修，M16.9 真跑发现并补）
  - `frontend/src/pages/bubble/{main,App}.tsx` + `bubble.html` 新增独立 webview entry
  - `frontend/src/components/pet/PetBubble.tsx` 去掉窗内 absolute / `flipBelow`、加 `ResizeObserver` → `invoke('set_bubble_size')`、`max-h-[440px] overflow-y-auto` 内部滚动
  - `frontend/src/pages/pet/App.tsx` 移除 `<PetBubble/>` 与 push subscriber 启动
  - `frontend/src/stores/petBubble.ts` 加 `attachBubbleWindowSync()`（phase → invoke show/hide）

- **AC 通过**：AC-1 / 2 / 3 / 7 / 8 / 9 / 10 真跑 + 单测 70 个全过；AC-4（拖拽 16ms tick 仍可感卡顿）/ AC-5（show_bubble 抢 chat 焦点，即使 macOS `orderFrontRegardless`）为已知行为接受、未阻塞合入。

- **延后**：hover 操作栏常驻问题（与 005 不直接相关，015/010 既有 UX 漂移）登 [issue 008](../008-pet-action-bar-hover-gate/)。
