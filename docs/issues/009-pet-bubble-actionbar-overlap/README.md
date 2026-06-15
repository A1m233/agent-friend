# 009 · pet bubble 与 ActionBar 在 Live2D 形象上方视觉重叠

- **状态**：open
- **严重度**：low
- **发现日期**：2026-06-15
- **发现于**：[018-pet-live2d-state-and-lipsync](../../requirements/018-pet-live2d-state-and-lipsync/) M18.9 真跑
- **关联需求**：[015-desktop-pet-bubble-and-conversation-owner](../../requirements/015-desktop-pet-bubble-and-conversation-owner/)（bubble 锚点）、[017-pet-overlay-form-switch](../../requirements/017-pet-overlay-form-switch/)（ActionBar 锚点）

## 现象

dev 模式下点 `inject_test_envelope` 注入长气泡测试时，**bubble 文本与 ActionBar（"打开对话界面 / 短气泡 / 长气泡"按钮组）在 Hiyori 形象头部上方位置重叠**——bubble 长文本背景半透明，部分文字盖住 ActionBar 按钮，按钮文字也被气泡背景挡。

附 18b M18.9 真跑截图：bubble 文本"这是一段超长测试文本…"挂在 Hiyori 头顶，ActionBar 三按钮也在 Hiyori 头部位置，两者垂直方向叠合。

## 根因初判

15/16 的 bubble window 跟 17a 的 ActionBar **都用同一个 sprite anchor** 作锚点（`spriteScreen` 矩形 / sprite top）：

- 015/016 `compute_bubble_position`：bubble 在 sprite top 上方
- 17a `computeActionBarPosition`：ActionBar 在 sprite top 上方

17a 占位时 sprite 是 160×160 圆形（小尺寸），两者上下间距小但视觉不明显；18b 上 Live2D 后 sprite anchor 区是 320×320，bubble 高度 + ActionBar 高度叠合时两者就糊在一起。

## 影响与判断

- 不影响功能（点击仍 OK，bubble 可读）。
- UX 体感差：dev 测试期间 bubble 显示时会遮 ActionBar 按钮可读性。
- 不阻塞 18b 任何 AC（视觉重叠不在 AC 范围）。
- 真实使用场景中 user 不会同时频繁触发"调试按钮"和"主动轮气泡"，重叠概率较低；但产品验证阶段值得修复。

## 可选修复方向

1. **bubble 上移 / ActionBar 下移**：让两者在垂直方向有 gap。bubble 锚点改为 sprite top - bubbleHeight - ACTIONBAR_HEIGHT；或 ActionBar 锚点改为 sprite top（贴形象顶）+ bubble 锚点改为 ActionBar top - gap。
2. **bubble 改用 sprite 旁锚点**：bubble 在形象**侧边**（如左侧或右侧）而非顶部，跟 ActionBar 完全分开。需要 015/016 设计扩展支持 anchor 方位。
3. **测试按钮单独藏起来**：dev 按钮不在 ActionBar 内，单独放角落（不抢 sprite top 锚点），只保留"打开对话界面"主按钮在 sprite 上方。

倾向方向 1（最小补丁）。
