---
name: tdesign-chat-style
description: 在 agent-friend 前端调整 TDesign Chat 对话组件具体视觉样式的标准流程。Use when 用户想修改对话页里的 TDesign Chat / ChatMessage / ChatSender / @tdesign-react/chat 样式，例如消息气泡、用户或助手消息、chatmessage base/text/outline variant、消息字号、行高、padding、间距、圆角、对齐、输入框外观、hover/focus 态，或用户提到 TDesign Chat 自定义样式；实现时优先用 CSS 变量，必要时用 CSS Parts / ::part。
---

# TDesign Chat 样式覆盖

## 核心路径

在 agent-friend 里调整 TDesign Chat 样式时，优先改 `frontend/src/styles/vendor/tdesign-chat.css`，并保持它由 `frontend/src/styles/index.css` 统一引入。不要把覆盖样式散写到页面组件、内联 style 或临时 class 里。

## 改前确认

1. 先读 `.cursor/rules/frontend-ui-conventions.mdc`，遵守项目 token 规则：视觉常量优先走 CSS 变量，不直接裸写颜色、字号、间距、圆角、阴影。
2. 查当前页面实际用到的 TDesign 组件和 variant：
   - `frontend/src/pages/chat/components/MessageList.tsx` 里 user / assistant 的 `variant` 会影响哪些变量生效。
   - user 的 `variant="base"` 可见气泡通常吃 `--td-chat-item-text-padding`。
   - assistant 的 `variant="text"` 通常吃 `--td-chat-item-content-padding`。
   - `--td-chat-item-content-base-padding` 主要影响 assistant 的 `base` / `outline` variant。
3. 用 TDesign 官方方式判断入口：优先 CSS Variables，变量不够时再用 CSS Parts。

## 推荐做法

### CSS Variables 优先

把变量覆盖集中写在：

```css
/* frontend/src/styles/vendor/tdesign-chat.css */
#root {
  --td-chat-font-size: var(--font-size-sm);
}
```

变量挂 `#root`，不要默认写 `:root`。本项目的 `chat/main.tsx` 先 import `@/styles/index.css`，再 import `@tdesign-react/chat/es/style/index.js`；TDesign 后加载的 `:root` 默认值可能覆盖我们自己的 `:root`。挂 `#root` 能让变量继承到 `t-chat-*` host，同时避开这个加载顺序问题。

常用变量：

- `--td-chat-font-size`：消息区字号，TDesign 默认 16px。
- `--td-chat-input-font-size`：ChatSender textarea 字号，TDesign 默认 16px。
- `--td-chat-item-text-padding`：user 文本气泡 padding。
- `--td-chat-item-content-padding`：assistant text variant 内容容器 padding。
- `--td-chat-item-content-base-padding`：assistant base / outline variant 内容容器 padding。

变量值优先引用项目 token 或 TDesign token，例如 `var(--font-size-sm)`、`var(--td-comp-paddingTB-s)`、`var(--td-comp-paddingLR-m)`。除非项目规则允许并且确有必要，不要直接写裸 `px`。

### CSS Parts 用于细节

当找不到合适变量时，再用 `::part()`。先在 Chrome DevTools 里查看 web component 的 `exportparts` / shadow DOM `part` 名称，再写精确选择器：

```css
t-chat-sender::part(t-chat__input__content) {
  border-radius: var(--radius-xl);
}
```

不要用普通 descendant selector 试图穿透 Shadow DOM；它不会稳定生效。

## 验证

1. 运行 `./scripts/frontend/lint.sh` 和 `./scripts/frontend/test.sh`。
2. 若样式看起来没生效，用浏览器 computed style 确认变量是否传到 `t-chat-item` / `t-chat-sender` host；再检查当前 variant 是否消费了目标变量。
3. 若改了 `.cursor/skills/tdesign-chat-style/SKILL.md`，按项目规则运行 `./scripts/codex-adapters/run.sh sync` 和 `./scripts/codex-adapters/run.sh doctor`。

## 参考入口

- 官方文档：https://tdesign.tencent.com/react-chat/custom-style
- 变量表：https://github.com/TDesignOteam/tdesign-web-components/blob/develop/src/chatbot/style/_var.less
- 本地源码可用 `rg -- '--td-chat-' frontend/node_modules/.pnpm/tdesign-web-components*` 搜索。
