# 002 · 对话页无法做"思考展示"：bridge 不发 reasoning 事件

- **状态**：open
- **严重度**：low
- **发现日期**：2026-06-10
- **关联需求**：010-desktop-shell-and-chat-ui（R-M3.3）

## 现象

需求 010 的 `R-M3.3 思考展示` 要求"AI 的思考 / reasoning 内容（AG-UI 对应事件）能在对话流中以可区分形式展示"。但落地 M10.3 时发现：**前端根本收不到任何 reasoning/thinking 事件**。

## 根因初判

agent-bridge 的 AG-UI 编码器（`agent_bridge/src/agent_bridge/protocols/ag_ui/encoders.py`）只把内部 `ConversationEvent` 映射成 `TEXT_MESSAGE_*` / `TOOL_CALL_*` / `RUN_*` 这几类 AG-UI 事件，**没有任何 reasoning/thinking 事件分支**——即便 agent 引擎产生思考流，也在编码器处被丢弃。因此对话页无 reasoning 数据可渲染。

## 影响与判断

- **不阻塞 M10.3 验收**：M3 的验收项 `AC-M3.1 ~ AC-M3.5` 里没有"思考展示"这条（AC-M3.3 是历史会话）。
- **不违反后端零侵入**：要做思考展示就得改 bridge 发 reasoning 事件，这会破坏 `R-M3.7 后端零侵入` / `AC-G.2`，故本期不做。
- 前端在对话消息结构 / 渲染层**预留思考内容的渲染位**，待后端补事件后接上即可，不需重构。

## 可选修复方向

1. **后端补 reasoning 事件**（需另立需求 / 放宽零侵入约束）：bridge 把 agent 的思考流映射为 AG-UI 自定义事件或 `TEXT_MESSAGE_*` 的可区分变体，前端按预留渲染位展示。
2. **接受为已知边界**：思考展示长期不做，仅保留前端渲染位与本记录。
