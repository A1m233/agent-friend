# 005 · PetBubble 应改用独立 window，而非加大 pet 主窗

- **状态**：open
- **严重度**：medium
- **发现日期**：2026-06-13
- **关联需求**：[015-desktop-pet-bubble-and-conversation-owner](../../requirements/015-desktop-pet-bubble-and-conversation-owner/)

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
