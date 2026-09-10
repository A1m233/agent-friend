# 上下文真实用量锚点与增量估算 - 技术方案

## 状态

CONFIRMED

## 需求文档

[requirement.md](./requirement.md)

## 现状与边界

Conversation 统一组装上下文，context/budget.py 为 summary、FIFO 等策略提供估算。现有 usage 只校准全量粗估且系数不能小于 1。消息还会经过 renderer 和 provider reasoning 投影，因此锚点不能只绑定业务 UUID。已有 session model history、rollout 和 assistant context_usage 提供恢复边界。

## 方案

- 在 context 内增加独立的用量锚点模块：保存版本、请求作用域摘要、输入消息数量、内容前缀摘要和可信输入 token；不保存另一份完整请求正文。
- 作用域包含会话、模型/连接路由、reasoning 配置、工具 schema 和请求设置。消息身份以统一 renderer 产生的模型输入为准；同 UUID 内容改变也使锚点失效。
- 仅当当前消息完整保留被 usage 覆盖的前缀、且作用域相同，估算为真实输入 token 加之后新增消息的保守估算。新 assistant 内容、tool calls、实际回放的 reasoning 与 tool results 属于增量；不直接添加 completion_tokens，也不扣除缓存命中。
- 输入 usage 必须是可信正整数且输入计费语义可识别。缺失或不兼容 usage 不建立新锚点。首轮、历史重写、模型/连接/工具变化、摘要和裁剪后的不兼容候选均采用全量安全估算。
- 保留旧校准作为无有效锚点时的安全回退；不再用它放大真实用量锚点。仅增量粗估保留保守系数。
- 通过 RuntimeContext 的可选估算入口让 summary、FIFO 和最终 notes 共用计量结果；独立使用 context manager 的调用方继续得到原有全量估算。
- 抽取统一消息渲染方法，预算候选与实际发送复用；压缩后的动态上下文重建也重新计量。发送处再次按最终消息、工具和设置复核；超过阈值进入既有一次性上下文恢复路径，不提交不可发送的 checkpoint，不无限重试。
- 每次模型调用前捕获对应请求摘要，完成事件将其与该次 usage 配对；重试重新捕获，防止与上次请求串联。成功锚点加入已有 assistant context_usage 元信息，跟随现有持久化流程；恢复只读取 active event 的候选，实际使用仍必须通过当前恢复历史的完整前缀校验。无法还原原模型历史的旧会话安全回退。
- 日志与 last_context_notes 增加估算模式、锚点输入、覆盖消息数、增量成本及回退原因。只记录摘要/数量，不记录消息正文或连接凭据。

## 涉及文件

| 范围 | 变更 |
|---|---|
| agent/context | 锚点纯逻辑、RuntimeContext 估算入口、预算说明 |
| agent/conversation.py | 请求捕获、统一渲染、使用/恢复锚点、最终检查及观测 |
| agent/tests | 高估反例、前缀/作用域变更、异常 usage、工具与附件、重启及最终检查 |

## 影响与验证

不改变工具对模型公开的契约、摘要策略、安全预留或依赖。普通 Python 业务逻辑属于 frozen runtime standard，不构建安装包。锚点格式为加性可选元数据，旧文件可读；未知版本回退。

重点验证：同一锚点对不同裁剪候选独立判定；renderer 不生成双份副作用；摘要调用不覆盖主请求锚点；缓存数据不从输入总量扣除；断流、取消与恢复不配错 usage；最终检查触发恢复的次数有界。对已恢复 rollout 和旧业务投影分别测试。所有新增测试用 fake，无外部 LLM 调用；完成后运行 backend、frontend、rust 三段项目门禁。

## 变更记录

| 日期 | 变更内容 | 是否需要重新实现 |
|---|---|---|
| 2026-09-10 | 用户确认锚点、严格前缀、安全回退、恢复和最终复核方向并授权实现 | 首次实现 |
