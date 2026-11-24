# 075 · 主动分享图片缺少桌面与 QQ 一致的呈现和投递支持

- **状态**：open
- **严重度**：medium
- **发现日期**：2026-11-21
- **关联需求**：169-autonomous-companionship；162-model-generated-image-delivery；181-proactive-message-markdown-content（收窄主动发送参数，跨渠道兼容仍延期）

## 现象

用户希望 AF 将分享的图片放在自己的消息正文中，并兼容本机对话与 QQ。当前主动发送工具 `send_conversation_message` 提供 `attachment_asset_ids`，桌面将这些图片作为正文前的独立附件卡片显示，QQ 则通过单独的图片上传链路发送，尚无一致的正文图片交付方式。

本次为源码核对，未新增真实桌面或 QQ 验收。核对基线为 `1ac7df6053141f6adde8088010162b34c02f6ab3`；下述相关文件与刷新后的 `origin/main`（`28728e8d91dca7960aedcef1dcd0bb767b9d74c4`）无差异：

- `agent/src/agent/tools/builtin/conversation_access/delivery.py`：公开附件参数，允许引用当前会话已有附件或工具图片的 `asset_id`。
- `agent/src/agent/sessions/manager.py`：`delivered_message_events` 写入的是 `assistant_message`，不是用户输入。
- `frontend/src/pages/chat/components/MessageList.tsx`：助手附件以单独的 `TextBubble role="assistant"` 放在消息正文之前。用户提出的“像用户侧附件”是呈现方面的反馈，不能据此认定持久化角色错误。
- `agent_bridge/src/agent_bridge/session_delivery/service.py`：`_deliver_im` 将附件解析为 `OutboundImage`；正文按 Markdown 发送，没有将正文中的图片引用解析为该图片对象。
- `agent_bridge/src/agent_bridge/protocols/im/adapters/qq.py`：仅在 `content.image` 存在时进入原件上传链路；Markdown 正文直接交给 QQ。
- `frontend/src/pages/chat/components/chatMarkdownConfig.ts`：现有 Markdown 配置没有提供与 QQ 上传对应的受管图片引用转换。

因此，把本地路径、附件 ID 或受管文件引用直接写进 `![说明](图片地址)`，不能视为已经完成跨渠道图片交付。公网图片 URL 在各客户端的实际可见性仍需分别验收；此记录不声称 QQ 平台完全不支持 Markdown 图片。

## 根因初判

已确认当前主动发送中的附件、桌面 Markdown 正文与 QQ 原生图片分别走不同的数据和呈现路径。图片引用的可访问性、消息中的位置及各渠道的发送结果没有统一契约。模型能写出 Markdown 语法，不等于宿主已解析、授权读取、上传并投递对应图片。

## 影响与判断

主动分享现有图片时，桌面正文体验与 QQ 图片投递能力不一致。仅移除附件参数并提示使用 Markdown，能够收窄工具能力面，但不会解决本 issue；此前依赖该参数的 QQ 原生图片主动发送能力也会随之收窄。

用户于本日明确决定暂缓兼容问题，另按需求推进“移除主动发送附件参数并说明 Markdown 用法”。本 issue 不要求删除历史附件或破坏已入队消息，也不把既有生图正常回复链路列为故障。

相关但独立的问题见 [issue 074](../074-tim-markdown-notification-unreadable/README.md)：TIM 无法显示既有 Markdown 通知消息。该问题已有真实客户端证据，本 issue 不替代或关闭它。

## 可选修复方向

后续评估结构化助手图片内容与标准 Markdown 语法树转换的适用边界，再确定桌面呈现、引用校验和 QQ 上传分别由谁负责；不在本次登记中锁定方案，也不采用未经验证的正则替换作为既定实现。

验证应覆盖本地与受管图片、公网图片、图文顺序、代码示例中的图片语法、历史恢复，以及上传失败、部分送达和重复发送，并分别记录桌面、QQ 与 TIM 的支持情况。
