# 074 · TIM 无法显示 QQ Markdown 通知正文

- **状态**：open
- **严重度**：medium
- **发现日期**：2026-11-20
- **关联需求**：无

## 现象

使用已登录的 Windows TIM 验收跨会话通知时，QQ 返回成功回执，但 TIM 仅显示“暂不支持该消息类型，请用手机 QQ 查看”，无法阅读正文。相同连接发送的普通文本绑定提示能够正常显示。

复现过程：

1. 启动 Tauri 开发端，在隔离数据目录复用本机 QQ 凭据，新建并绑定测试会话。
2. 从 TIM 发起命令授权请求，使目标会话保持等待授权；不执行命令。
3. 从另一桌面会话通过 `list_conversations` 发现目标，再用 `send_conversation_message` 发送普通文字，指定 `notify_im=true`。
4. QQ 接口成功，TIM 出现不支持消息类型的占位；拒绝授权后，桌面会话正常补入通知正文。

本次证据来自 `fix/queued-im-notification-delivery`，基线 `bd815c88` 加当时未提交的投递修复。投递编号 `0d80eb42-74c4-4973-935f-7034e9b92d58`：22:09:31.253 接受，22:09:32.107 为 `queued / delivered`，22:10:03.843 为 `committed / delivered`，同一回执未变化。日志记录 `text_format=markdown`、QQ HTTP 200，桌面最终正文仅一份。

原始截图、状态和日志保留在本机私有验收目录，包含私有上下文，不纳入仓库；本机临时记录不是永久证据存储。

## 根因初判

`agent_bridge/src/agent_bridge/session_delivery/service.py` 的 `_deliver_im` 固定使用 `text_format="markdown"`。本次实际 TIM 客户端不支持该消息类型；正文没有 Markdown 语法也仍按 Markdown 消息发送。QQ 服务端的成功回执只能证明接收，不能证明客户端已展示正文。

## 影响与判断

本机 TIM 的通知可读性受影响；尚未验证其他 TIM 版本和手机 QQ。此次只修复排队投递丢失，客户端消息格式兼容属于独立问题，暂不混入该修复。排队投递机制通过不等于 TIM 用户体验通过。

## 可选修复方向

评估普通文本发送或客户端能力降级，并使用真实 TIM 验证正文可见；同时检查格式、链接、图片及重复发送边界。不要把 QQ 成功回执直接解释为用户已读。
