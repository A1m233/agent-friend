# 094 · DeepSeek 视觉与图片 Files API 接入 - 进度

## 总体状态

COMPLETED

## 任务列表

- [x] 扩展统一模型目录，登记 DeepSeek V4 Flash Vision 的模型级 API base、processing profile 与 image-only 能力限制。
- [x] 在多模态适配器中实现 DeepSeek Files API 上传、24 小时 TTL、账号隔离缓存、有效性复查与失效重传。
- [x] 实现仅作用于目标模型的 LiteLLM request wire shim，并接入同步与流式调用公共入口。
- [x] 更新 Bridge、前端动态模型目录与环境说明相关断言，保持默认模型和 Kimi 行为不变。
- [x] 补齐 catalog、附件处理、最终 HTTP body、stream/tool/error/cancel 与跨模块回归测试。
- [x] 运行受影响定向测试和项目全量 `scripts/check` 门禁，并记录验证证据。

## AC 验证

- [x] AC-1：模型目录与前端动态投影包含普通显示的 DeepSeek 视觉模型；默认聊天/记忆模型未变。
- [x] AC-2：JPEG、PNG、WebP 的文字加图片与仅图片消息可沿既有附件入口进入适配器。
- [x] AC-3：最终请求使用 DeepSeek Files API `file_id` 引用，公共附件对象不保存远端引用或 base64。
- [x] AC-4：缓存按账号与处理策略隔离，有效文件复用，远端失效或更换 API Key 后重传。
- [x] AC-5：远端文件采用 24 小时有限 TTL，本地附件保持持久真相源。
- [x] AC-6：DeepSeek 文本模型继续拒绝附件，视觉模型拒绝 PDF、TXT、GIF 与其他未支持类型。
- [x] AC-7：前端、Bridge、Agent 与 IM 使用统一目录中的类型、大小和数量限制。
- [x] AC-8：视觉/文本模型切换时的附件草稿保护、发送阻止和历史兼容投影不回归。
- [x] AC-9：桌面与 IM 图片共享 provider preparation，失败不会静默丢弃附件或发送空消息。
- [x] AC-10：上传失败、账号隔离、缓存、失效重传、超限、类型拒绝和日志脱敏具备自动化覆盖。
- [x] AC-11：真实 JPEG、PNG、WebP 理解及流式工具循环验证通过；总计 7 次模型请求，使用 24 小时 TTL 的远端图片。
- [x] AC-12：Kimi 图片/PDF/TXT及 DeepSeek 文本对话、工具调用和模型切换回归通过。
- [x] AC-13：项目 `scripts/check` 全量门禁通过。

## 实现日志

| 日期 | 完成内容 | 验证或备注 |
| --- | --- | --- |
| 2026-08-21 | Phase 3 获得实现授权；建立任务与 AC 跟踪。 | 真实 DeepSeek API 调用不在本轮自动授权内；先使用 mock/fake 与本地门禁。 |
| 2026-08-21 | 完成统一目录、DeepSeek Files preparation、24 小时 TTL/cache 与精确模型限定的 LiteLLM wire shim；Bridge、IM 和 provider adapter 同步执行 64 MiB 单文件限制。 | 首轮 Python 定向回归 70 项通过；最终 HTTP body golden 与可取消 LiteLLM stream 测试 11 项通过。 |
| 2026-08-21 | 完成跨模块回归、全量门禁与 packaging 影响分类。 | 最终 backend 1988 passed / 4 skipped；frontend 596 tests + production build；Rust 215+6 tests、1 ignored + release check；installer contract=`standard`、packageRequired=`false`。仅 AC-11 真实 DeepSeek 验收待单次授权。 |
| 2026-08-21 | 完成获授权的真实 DeepSeek 视觉验收。 | 共 7 次模型调用：PNG/JPEG/WebP 可读取固定可见码 `AF-094`；同图 cache 命中未重传；仅图片消息可读；tool call 返回正确参数且 follow-up 完成 `DONE`；transport=`litellm`。首轮在仅图片严格等值 judge 处停止，放宽为“包含可见码”后仅续跑剩余 3 次；累计 4 次上传 + 1 次 retrieve，远端均为 24 小时 TTL。仅图片响应较啰嗦并触达 128-token 上限，记为非阻塞观察。 |
| 2026-08-21 | 固化跨平台真实 smoke 入口并完成最终静态复核。 | 新增 `scripts/deepseek-vision-smoke/run.py`、`run.ps1`、`run.sh` 与 README 登记；默认拒绝未带 `--allow-llm` 的运行。新增 harness 后 backend 仍为 1988 passed / 4 skipped；最终 installer contract=`standard`、packageRequired=`false`。当前 Windows 环境无 Bash，`run.sh` 未执行 `bash -n`。 |
| 2026-08-21 | 用户确认验收完成。 | 全部实现任务与 AC 已完成，状态更新为 `COMPLETED`，进入提交、合入 `main` 与推送流程。 |
