# 123 实现进度

## 总体状态

COMPLETED

## 任务列表

- [x] 需求、方案确认，独立分支远程占号
- [x] 实现锚点与请求增量计量
- [x] 接入上下文入口、持久化恢复及最终预算复核
- [x] 补充确定性回归测试
- [x] backend / frontend / rust 项目门禁
- [x] 用户验收

## AC 验证

| 条目 | 当前证据 |
|---|---|
| AC-1 | 新增高估反例：111680 的真实锚点加 8000 字符工具结果仍低于 209716；再追加 150000 字符会达到阈值。是合成机制回归，不是原会话精确 replay。 |
| AC-2 | 完整 calculate 工具循环输出 391、连续对话、输入输出与缓存计量断言通过。 |
| AC-3 | 真实 rollout 落盘/恢复后的连续对话命中锚点；前缀编辑、删除、重排、系统变化和调用作用域变化回退。既有压缩 checkpoint / edit-resend 测试在全量门禁中复核。 |
| AC-4 | 异常/缺失 usage、未知版本、旧元数据、规范化 inclusive/exclusive 缓存输入、opaque provider capability 的降级断言通过。 |
| AC-5 | 附件候选不会触发上传；native file 使用来源预算而非 ID/base64 长度；reasoning 文本和图片预算断言通过。已有 summary/FIFO 测试通过专项复验。 |
| AC-6 | 新增 estimation_mode、anchor_input_tokens、anchor_covered_messages、incremental_tokens、anchor_fallback_reason；请求日志和最终 notes 同源。锚点元数据只存摘要/计数，不存正文。 |
| AC-7 | frontend、rust、backend 三段门禁全部通过。用户在真实 smoke 通过后明确要求合入主干并推送，验收完成。 |

新增文件 `agent/tests/test_context_usage_anchor.py` 共 26 项通过；连同架构边界测试的专项命令共 27 项通过。自动化阶段未调用真实 LLM；之后按用户明确授权完成下述真实 smoke。未启动开发桌面或执行视觉验收；本次没有 UI 变更。

## 真实 smoke

2026-09-10 22:42 +08:00，运行 `scripts/context-usage-smoke/run.ps1 --allow-llm --model kimi/kimi-k2.7-code-highspeed --output docs/requirements/123-context-usage-anchor-and-incremental-estimation/artifacts/real-usage-smoke-20260910-01.json`，退出码 0。

| 阶段 | 旧全量估算 | 本次实际采用估算 | provider 实际输入 | 本次估算误差 |
|---|---:|---:|---:|---:|
| 建立锚点 | 39871 | 39871（无锚点） | 14434 | +176.2% |
| 工具调用请求 | 39932 | 14689 | 14533 | +1.1% |
| 工具结果续轮 | 40082 | 14833 | 14631 | +1.4% |
| rollout 重载后 | 40099 | 14739 | 14682 | +0.4% |

- 4 次实际请求，输入合计 58280、输出合计 217 token；禁用自动重试，脚本硬上限 5 次。模型 reasoning_preset=on，固定输出上限 2048。
- 40000 字符合成 ASCII 背景，只有本地 calculate 工具；没有读取或发送真实用户会话。工具实际执行 17*23 并返回 391，重载后答案仍为 391。
- 为区别旧估算与新机制，在首次真实 usage 后将隔离 client 的逻辑窗口设为 33940、阈值 27152；provider 自身窗口仍为 262144，未修改产品设置。后续三次均使用 usage_anchor，持久化压缩数为 0。
- 重载使用新的 Conversation、store 与已验证 rollout，在同一进程中完成；不冒充杀进程重启或桌面端验收。单次合成 smoke 证明这一路由的真实链路，不外推所有 provider、附件或长期压缩频率。
- 原始脱敏报告见 [real-usage-smoke-20260910-01.json](./artifacts/real-usage-smoke-20260910-01.json)，含实际实现和 harness 的 SHA-256。

## 兼容与降级边界

- provider 层以 `LanguageModelCapabilities.input_usage_scope` 声明可否按完整客户端请求计量；未知实现与当前 opaque continuation 路由安全回退。Conversation 不按厂商/API 名分支。
- 附件候选预算阶段不执行 renderer 的上传副作用，仍采用原始附件保守预算；原生 file 输入最终检查保留来源预算。含附件历史可能仍先走全量估算，不宣称这类场景获得与纯文本同等的压缩频率改善。
- 旧会话没有可信锚点时保持可用；下次取得可兼容 usage 后建立新锚点。模型/工具/设置或前缀变化时不以估算差额修补旧锚点。

## 实现日志

| 日期 | 完成内容 | 验证或备注 |
|---|---|---|
| 2026-09-10 | 用户授权落盘方案并实现 | standard 打包影响，无依赖/资源或打包契约变更 |
| 2026-09-10 | 完成锚点、预算入口、恢复、最终发送检查及日志 | metadata 为可选加性字段，未构建安装包 |
| 2026-09-10 | 初轮全量门禁发现架构边界检查失败，已将协议判定收回 provider capability | 专项复验通过；未绕过或放宽边界测试 |
| 2026-09-10 | 初轮全量中 50ms idle timeout 测试触发额外重试，单文件 57 项复跑通过 | 当时并发前端/Rust 构建；负载影响为推断，未修改超时测试或产品重试逻辑；最终全量复验在构建结束后单独执行 |
| 2026-09-10 | frontend 门禁通过 | lint/typecheck，150 文件 / 1244 测试，production build；保留已有 CSS highlight 与包体警告 |
| 2026-09-10 | rust 门禁通过 | cargo fmt、282 tests、cargo check --release；构建目录在仓库外，保留已有 dead_code 警告 |
| 2026-09-10 | 最终 backend 门禁通过 | scripts/check/run.ps1 --stage backend：ruff + format、mypy（672 文件）、2992 passed / 5 skipped；保留第三方异步清理与 ZIP fixture 警告，退出码 0 |
| 2026-09-10 | 验证源码清单对账通过 | artifacts/candidate-source.sha256 的 8 个文件均未变化；未暂存或提交实现，未合入 main，待用户验收 |
| 2026-09-10 | 用户要求真实 smoke，新增双平台 context-usage-smoke 入口并完成 4 次真实 Kimi 请求 | smoke passed；产品代码未再改动；新增 harness 的 lint、格式、带项目根解析的 mypy（673 文件）通过 |

| 2026-09-10 | 用户确认真实 smoke 后授权合入主干并推送 | 验收完成；最新 origin/main 仅增加 121 交付文档，已验证产品代码没有变化 |
| 2026-09-10 | merge-main-and-push Guide 复核：无需更新 | 公开基线 112553f3、Guide cb528b7b；基线后的安装矩阵脚本改动属于开发验证，123 只调整内部预算计量；using-agent-friend.md 的整理记录、settings/model-credentials 的窗口说明仍准确，无新操作或隐私边界 |
