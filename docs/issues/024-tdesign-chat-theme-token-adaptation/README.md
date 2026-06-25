# 024 · TDesign Chat 组件主题 Token 适配

- **状态**：resolved
- **严重度**：medium
- **发现日期**：2026-06-23
- **关联需求**：028-settings-center-and-cross-window-sync

## 现象

在 028 需求实现与验证过程中发现：`tdesign-chat` 组件库的 UI 在应用主题切换（light/dark）时没有正确跟随项目主题 token。

具体表现：
- 聊天气泡、输入框、背景等区域的主题样式与项目整体风格不一致。
- 切换主题为 dark 后，`tdesign-chat` 相关区域仍保持 light 风格或显示硬编码颜色，造成明显视觉撕裂。

## 根因初判

`tdesign-chat` 是第三方组件库，其内部样式可能：
1. 直接使用硬编码颜色值；
2. 或使用自身独立的 CSS 变量体系；

而未消费本项目通过 `html[theme]` 机制暴露的语义化设计 token（如 `--bg`、`--fg`、`--surface`、`--border` 等，详见 027-frontend-design-token-extraction）。

## 影响与判断

- **UI 一致性**：主题切换时部分区域视觉体验割裂，影响整体产品品质。
- **维护成本**：如果通过覆盖 `tdesign-chat` 内部样式来适配，容易与组件库升级产生冲突，需要持续跟进。
- **暂不修**：本期 028 聚焦于设置中心架构与跨窗口同步，`tdesign-chat` 主题适配属于独立范围，延后处理。

## 可选修复方向

1. **官方主题配置**：调研 `tdesign-chat` / `tdesign-vue-next` 是否提供主题配置或 CSS 变量覆盖能力，优先使用官方方案。
2. **CSS 变量覆盖**：若官方不支持，评估通过全局 CSS 或 Tailwind 规则覆盖 `tdesign-chat` 内部颜色变量。
3. **组件二次封装**：对 `tdesign-chat` 做项目级薄封装，在封装层统一注入主题 token。
4. **替换组件**：若适配成本过高，评估是否有更贴合项目主题体系的替代方案。

---

已在分支 `fix/024-tdesign-chat-theme-token-adaptation` 修复，并于 2026-06-26 通过桌面端 dark 主题验收。
