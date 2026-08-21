# DeepSeek 视觉与图片 Files API 接入 - 证据索引

## 用途与范围

- 档案归属：[requirement.md](./requirement.md)
- 本索引记录：DeepSeek 视觉模型和 Files API 的 2026-08-21 官方契约、本地附件与 Provider 代码现状、Kimi 对照实现及当前需求决定。
- 本索引不记录：API Key、完整图片或模型请求/响应、真实模型调用结果、技术方案和实现进度。
- 最近对账：2026-08-21，基线 `170e1d7005a7feb17359659134c3b823030f7efb`、当前实现与已确认需求/方案一致。

## 当前综合判断

- DeepSeek 于 2026-08-21 新增 `deepseek-v4-flash-vision-exp`，这是当前唯一应在 agent-friend 中声明图片输入能力的 DeepSeek 模型；V4 Flash、V4 Pro 仍保持纯文本能力。
- DeepSeek Files API 当前只上传 JPEG、PNG、GIF、WebP 图片，不是通用文档输入 API；Responses API 也明确不支持普通 file input。因此本需求只能承诺 agent-friend 现有的 JPEG、PNG、WebP，不能据 Files API 名称推导 PDF/TXT 支持。
- 059、060 已经完成附件 UI、本地托管、历史恢复、能力目录和 IM 输入；新增需求应集中在 DeepSeek 模型路由、图片 Provider adapter、远端缓存和失效恢复。
- Kimi 当前实现已经具备按附件和账号隔离的 file ID 缓存、复用前有效性检查和失效重传，但实际代码没有设置远端过期时间，也没有调用远端删除。DeepSeek 应复用缓存与恢复模式，并利用其原生过期能力避免永久保存；本需求不顺带修改 Kimi。
- 当前 LiteLLM 锁版本、最新稳定版、当天预发布版和上游主线都不原生支持该视觉模型：DeepSeek transformation 会丢弃 content list，通用 OpenAI transformation 又只接受嵌套 `file.file_id`。本地验证表明可以继续走 LiteLLM，并用有界 `extra_body.messages` shim 只覆盖最终 DeepSeek wire messages；无需升级 LiteLLM 或维护 direct stream transport。
- 当前实现已经把临时结论固化为自动化契约：MockTransport 捕获的真实 LiteLLM/OpenAI SDK 请求只包含 DeepSeek 扁平 `file_id`，同步、普通流与可取消流均保留现有 LiteLLM 边界；Files preparation、账号隔离 cache、24 小时 TTL、临期/失效重传和格式限制均有确定性测试。
- 获授权的真实 smoke 已验证线上契约：PNG/JPEG/WebP 均能读取固定可见码；同图复用不重传；仅图片消息可用；强制函数调用能给出正确参数并完成 tool follow-up；所有观测 transport 均为 `litellm`。

## 证据账本

| ID | 日期 | 问题或主张 | 类型 | 关键锚点 | 摘要 | 置信度 | 状态 | 详细记录 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| E-001 | 2026-08-21 | DeepSeek 当前是否已经提供视觉模型 | 外部资料 | DeepSeek Change Log，2026-08-21 条目 | 官方发布 `deepseek-v4-flash-vision-exp`，并称其纯文本能力与 V4 Flash 相当、视觉 Agent 能力显著增强 | confirmed | active | https://api-docs.deepseek.com/updates/ |
| E-002 | 2026-08-21 | 哪些 DeepSeek 模型接受图片 | 外部资料 | DeepSeek Models & Pricing / Vision，采集于 2026-08-21 | 目录列出 V4 Flash、V4 Pro 和 V4 Flash Vision Exp；Vision 指南只允许视觉模型处理图片，其他模型拒绝图片 | confirmed | active | https://api-docs.deepseek.com/quick_start/pricing/；https://api-docs.deepseek.com/guides/vision |
| E-003 | 2026-08-21 | 视觉模型支持哪些图片输入方式和格式 | 外部资料 | DeepSeek Vision guide | 支持 base64、外部 URL、Files API `file_id`；格式为 JPEG、PNG、GIF、WebP，并规定图片、请求总量和尺寸限制 | confirmed | active | https://api-docs.deepseek.com/guides/vision |
| E-004 | 2026-08-21 | DeepSeek Files API 的存储与生命周期契约 | 外部资料 | DeepSeek Files API guide | `POST /files` 使用 `purpose=user_data`；单文件 64 MiB；支持 1 小时至 30 天过期或永久保存；可 list/retrieve/delete，账号上限 25 GiB、10,000 文件 | confirmed | active | https://api-docs.deepseek.com/guides/files_api |
| E-005 | 2026-08-21 | Files API 是否代表通用 PDF/TXT 文件输入 | 外部资料 | DeepSeek Files API + Responses API | Files API 指南只描述图片；Responses compatibility 明确 file inputs 不支持，因此不能据接口名称承诺文档输入 | confirmed | active | https://api-docs.deepseek.com/guides/files_api；https://api-docs.deepseek.com/guides/responses_api/ |
| E-006 | 2026-08-21 | agent-friend 是否已有附件产品主链路 | 代码/需求 | 基线 `170e1d7`，059/060 状态 COMPLETED | 图片/文档选择、拖放、粘贴、本地托管、历史恢复、能力校验、IM 和 Kimi 真模型验证已经完成 | confirmed | active | `../059-multimodal-message-attachments/`；`../060-composer-attachment-inputs/` |
| E-007 | 2026-08-21 | 当前模型目录如何描述 DeepSeek 与 Kimi 附件能力 | 代码 | `170e1d7` `catalog.py`，revision `2026-08-20.1` | DeepSeek Flash/Pro 使用 text-only capabilities；Kimi 使用 image+document capabilities；目录是公共能力事实源 | confirmed | active | `../../../llm_providers/src/llm_providers/catalog.py` |
| E-008 | 2026-08-21 | 当前多模态 adapter 能否处理 DeepSeek | 代码 | `170e1d7` `multimodal/adapter.py` | adapter 对 attachments 仅允许 provider `kimi`，DeepSeek 会返回 `attachment_model_unsupported`，因此新增目录项本身不足以完成接入 | confirmed | active | `../../../llm_providers/src/llm_providers/multimodal/adapter.py` |
| E-009 | 2026-08-21 | Kimi 当前如何缓存与恢复远端文件 | 代码 | `170e1d7` adapter + tests | cache key 包含 asset、账号摘要、transport、profile 和 purpose；图片复用前 retrieve，失败后删本地 cache 并重新上传；single-flight 锁避免重复上传 | confirmed | active | `../../../llm_providers/src/llm_providers/multimodal/adapter.py`；`../../../llm_providers/tests/test_multimodal_adapter.py` |
| E-010 | 2026-08-21 | Kimi 当前是否落实远端过期或删除 | 代码/设计对账 | `170e1d7`，全仓搜索 `expires_after` / `files.delete` 无实现 | 059 design 要求本地 GC 时 best-effort 远端删除，但当前代码只移除 provider cache；上传也未设置过期。本需求不复制这一生命周期缺口，也不负责修复 Kimi | confirmed | active | `../059-multimodal-message-attachments/design.md`；`../../../agent/src/agent/attachments/store.py`；`../../../llm_providers/src/llm_providers/multimodal/adapter.py` |
| E-011 | 2026-08-21 | 当前 LiteLLM 是否已经认识新视觉模型 | 代码/外部资料 | `uv.lock` LiteLLM 1.83.14，发布早于 2026-08-21；LiteLLM custom model docs | 初步判断未知模型可显式注册元数据，但尚未证明 DeepSeek `file` 内容块无损透传；后续 E-013～E-016 已完成验证并取代本条 | directional | superseded | `../../../uv.lock`；https://github.com/BerriAI/litellm |
| E-012 | 2026-08-21 | 同类 Responses API 如何区分 image、file 与 file search | 外部资料 | OpenAI Responses reference，采集于 2026-08-21 | 官方接口把 text/image/file input 与 file search 分成不同内容和工具语义，支持本需求继续按能力类型显式建模，而不是用“上传文件”布尔值概括 | strongly-supported | retained | https://developers.openai.com/api/reference/cli/resources/responses/methods/create |
| E-013 | 2026-08-21 | LiteLLM 最新稳定版是否原生支持 DeepSeek 视觉模型 | 外部源码/包 | PyPI 1.97.0，上传于 2026-08-16；Git tag `v1.97.0`=`ef84494d52c6708e4e9f4a54ce551a265995ad8f` | 模型目录没有 vision-exp；DeepSeek transformation 仍无条件把 content list 转成纯文本，不能承载图片或 file block | confirmed | active | https://pypi.org/project/litellm/1.97.0/；https://github.com/BerriAI/litellm/blob/v1.97.0/litellm/llms/deepseek/chat/transformation.py |
| E-014 | 2026-08-21 | 当天预发布版和上游主线是否已抢先支持 | 外部源码/包 | PyPI 1.99.0.dev2，上传于 2026-08-21 03:19 UTC，Windows wheel SHA-256 `a910d5f094a97235586b06dd39885d69468fcc375b27f0b8668f6870c377fc5b`；GitHub main/staging | wheel 全包没有 `deepseek-v4-flash-vision-exp` 字符串，transformation 仍保留 content-list-to-string；main/staging 同样如此 | confirmed | active | https://pypi.org/project/litellm/1.99.0.dev2/；https://github.com/BerriAI/litellm/blob/main/litellm/llms/deepseek/chat/transformation.py |
| E-015 | 2026-08-21 | 不走 DeepSeek transformation 时 LiteLLM 能否保留标准多模态内容 | 本地受控验证 | 基线 `170e1d7`，LiteLLM 1.83.14 + OpenAI SDK 2.24.0 + `httpx.MockTransport` | `model=openai/deepseek-v4-flash-vision-exp`、显式 DeepSeek api_base 时，`image_url` content list 被原样发送到 `/chat/completions`，响应继续由 LiteLLM 规范化 | confirmed | active | 未归档临时输出；实现阶段将固化为 `llm_providers/tests/test_deepseek_vision_compat.py` |
| E-016 | 2026-08-21 | Files API 扁平 file block 能否在不绕开 LiteLLM 的前提下发送 | 本地受控验证 | 当前锁版本；主 messages 使用嵌套 `file.file_id`，`extra_body.messages` 使用扁平 `file_id` | 最终 HTTP body 只包含 DeepSeek 扁平 file block，并保留官方模型名、`thinking.type=disabled`、tools、stream 与 stream options；同步和可取消路径继续使用 LiteLLM | confirmed | active | `../../../llm_providers/tests/test_deepseek_vision_compat.py` |
| E-017 | 2026-08-21 | Files preparation 与附件边界是否满足已确认方案 | 自动化测试 | provider catalog、multimodal adapter、Bridge admission、IM router 与前端 catalog tests | 覆盖 24 小时 TTL、5 分钟临期窗口、retrieve 失效重传、API Key 隔离、single-flight、JPEG/PNG/WebP、PDF/TXT/GIF 拒绝、64 MiB 单文件限制以及 DeepSeek IM 正向准入 | confirmed | active | `../../../llm_providers/tests/test_multimodal_adapter.py`；`../../../agent_bridge/tests/test_attachment_inputs.py`；`../../../agent_bridge/tests/test_im_router.py`；`../../../frontend/src/lib/model-config/index.test.ts` |
| E-018 | 2026-08-21 | 项目全量回归是否通过 | 自动化门禁 | `scripts/check` 分段执行 | 最终 backend 1988 passed / 4 skipped；frontend 103 files、596 tests、production build 通过；Rust 215+6 tests 通过、1 ignored，release check 通过 | confirmed | active | 本地命令输出；命令与结果同步记入 [progress.md](./progress.md) |
| E-019 | 2026-08-21 | 是否需要冻结 runtime 或安装包验证 | 机器契约 | `scripts/package/run.ps1 installer-contract` | classification=`standard`、packageRequired=`false`，没有 package-sensitive/full-matrix 路径；未新增依赖、动态导入或 package data | confirmed | active | 本地 installer-contract 输出；最终工作树 package input hash `52938bcb01c6eb8d1f179268dae9f78a7aa5387c0a8a6305647b37dde533909b` |
| E-020 | 2026-08-21 | DeepSeek 线上 Files/vision/stream/tool 契约是否实际可用 | 获授权真实 smoke | `scripts/deepseek-vision-smoke/run.ps1 --allow-llm`；固定可见码 `AF-094` | 共 7 次模型请求：PNG/JPEG/WebP、同图复用、仅图片、tool call 与 tool follow-up 均通过；累计 4 次上传 + 1 次 retrieve，远端 24 小时 TTL；tool 参数正确，follow-up 返回 `DONE`，transport=`litellm` | confirmed | active | [progress.md](./progress.md) 仅保存最小结果；不归档图片正文、file ID、API Key 或完整模型响应 |

## 未决问题

| 问题 | 当前无法区分的解释 | 最有区分力的下一项证据 | 阻塞情况 |
| --- | --- | --- | --- |
| `extra_body.messages` 在未来 LiteLLM/OpenAI SDK 升级后是否仍覆盖最终 wire messages | 当前锁版本已由 E-016 固化；未来 SDK merge 顺序或 LiteLLM validation 可能变化 | 任何依赖升级必须继续通过最终 HTTP body golden test | 不阻塞当前实现；升级时必须重新验证 |
| 仅图片短指令的输出约束是否稳定 | 当前真实调用正确识别 `AF-094`，但返回较长文本并触达 128-token 上限；可能是实验模型的指令遵循波动 | 后续模型升级或产品出现实际体感问题时，用同一 smoke 观察输出长度与 stop reason | 非阻塞；不影响视觉、Files、stream 或 tool-loop 可用性 |

## 决策影响

| 决定 | 依赖证据 | 当前状态 | 重新打开条件 |
| --- | --- | --- | --- |
| 新增 DeepSeek 视觉模型，但不增加“实验”界面标签 | E-001、用户 2026-08-21 确认 | 当前 | 用户改变产品展示要求或官方改为另一稳定模型标识 |
| 首期只支持 JPEG、PNG、WebP | E-003、E-006、用户 2026-08-21 确认 | 当前 | agent-friend 独立开放 GIF，或 DeepSeek 提供受治理的文档输入能力 |
| PDF/TXT 继续只由声明文档能力的模型处理 | E-005、E-007 | 当前 | DeepSeek 官方新增并验证普通文件输入 |
| 复用现有附件 UI、托管和历史语义，不新建上传体系 | E-006、E-007、E-008 | 当前 | 现有公共附件契约无法表达 DeepSeek 图片且得到代码证据证明 |
| 复用 Kimi 的账号隔离缓存和失效重传模式 | E-009 | 当前 | DeepSeek Files API 不允许 retrieve/复用，或真实验证表明该模式不可靠 |
| DeepSeek 远端文件必须有限期，不复制 Kimi 永久上传现状 | E-004、E-010、用户 2026-08-21 确认 | 当前 | DeepSeek 移除过期能力且没有等价删除机制，需要重新确认隐私边界 |
| 不在 094 中顺带修复 Kimi 远端生命周期 | E-010、用户确认的需求范围 | 当前 | 用户明确要求扩大 094，或另开独立修复任务 |
| 不预先承诺 LiteLLM 或直连 SDK 路径 | E-011 | 已取代 | E-013～E-016 已完成区分性验证 |
| 视觉模型继续使用 LiteLLM 通用 OpenAI-compatible transport，不升级依赖、不新增 direct stream transport | E-013、E-014、E-015、E-016 | 当前 | LiteLLM 稳定版原生支持 DeepSeek vision file block 并通过项目 golden/stream/tool tests |
| 在项目边界使用有界 `extra_body.messages` wire shim | E-005、E-013、E-014、E-016 | 当前 | DeepSeek 改用标准嵌套 file block，或 LiteLLM 原生生成当前扁平格式 |
| DeepSeek 远端文件固定 24 小时 TTL，并在不足 5 分钟时提前重传 | E-004、E-009、E-017、已确认 design | 当前 | 隐私边界、跨日复用数据或 DeepSeek TTL 合约发生变化 |

## Artifact 映射

| 证据 ID | 原始位置 | 归档位置 | 完整性 | 敏感性与访问说明 |
| --- | --- | --- | --- | --- |
| E-001–E-005 | DeepSeek 官方公开 URL，采集于 2026-08-21 | 未保存网页快照；索引保留 URL、日期和最小摘要 | 未校验内容 hash；页面可能随新发布继续更新 | 公开，无凭据 |
| E-006–E-011 | 私有仓库基线 `170e1d7005a7feb17359659134c3b823030f7efb` | 当前 Git 历史与相对路径 | commit SHA 锚定；E-010 另经全仓符号搜索核对 | 私有仓库代码，不含 secret 内容 |
| E-012 | OpenAI 官方公开 API reference | 未保存网页快照 | 未校验内容 hash | 公开，无凭据 |
| E-013–E-014 | PyPI 官方包元数据与 BerriAI/litellm 官方 tag/main/staging | 未保存完整 wheel；索引保留版本、时间、tag/SHA 与 wheel hash | tag/包 SHA 可复核；网页和 main 会演进 | 公开，无凭据 |
| E-015–E-016 | 本地 MockTransport 受控请求 | E-016 已固化为仓库 golden tests；E-015 保留为设计期 image_url 对照 | E-016 由锁定依赖和最终 HTTP body 断言持续复核 | 私有本地验证，使用 fake key/file ID，无真实图片或外部调用 |
| E-017 | 当前工作树自动化测试 | 仓库测试文件 | 断言级 artifact，可由 `scripts/test`/`scripts/check` 重放 | 私有仓库代码；测试只使用内存 fake/MockTransport |
| E-018–E-019 | 当前工作树项目门禁与 installer contract | [progress.md](./progress.md) 最小结果摘要 | 未保存完整终端日志；命令、计数、分类与 package input hash 可复核 | 本地构建证据，不含凭据或用户数据 |
| E-020 | 获授权的真实 DeepSeek smoke | [progress.md](./progress.md) 最小观察摘要；可执行入口在 `../../../scripts/deepseek-vision-smoke/` | 保留场景、计数、usage/stop reason 与通过状态；不保留完整响应 | 使用系统 keyring 凭据但不读取或输出明文；临时图片和本地 cache 随运行目录删除，远端文件按 24 小时 TTL 自动过期 |

## 对账记录

| 日期 | 对账范围 | 结果与修订 |
| --- | --- | --- |
| 2026-08-21 | DeepSeek Change Log、Models、Vision、Files API、Responses API；059/060；模型目录；多模态 adapter；Kimi 测试；附件 GC；LiteLLM 锁版本；已确认产品边界 | 一致；确认 Files API 当前只支持图片，094 复用现有附件能力并新增 DeepSeek 图片 provider 处理；Kimi 远端清理差异留在需求范围外 |
| 2026-08-21 | LiteLLM 1.97.0、1.99.0.dev2、main/staging、当前 1.83.14 OpenAI/DeepSeek transformations；MockTransport 最终 body | 修订传输结论：升级不能获得原生支持；通用 OpenAI-compatible route + 有界 wire shim 可继续复用 LiteLLM，并满足 DeepSeek Files API 扁平 file block |
| 2026-08-21 | 当前实现、定向测试、最终 HTTP body golden、`scripts/check` backend/frontend/rust、installer contract | 实现与已确认 design 一致；未升级 LiteLLM、未新增 direct transport；全量自动化通过且 packaging 分类为 standard。剩余缺口仅为需单次授权的真实 DeepSeek AC-11 |
| 2026-08-21 | 获授权真实 DeepSeek Files/vision/stream/tool smoke | AC-11 通过；真实线路与本地 golden 一致，继续走 LiteLLM。记录仅图片短指令输出偏长为非阻塞观察，不扩大本需求范围 |
