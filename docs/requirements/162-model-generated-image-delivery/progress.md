# 162 · 模型生成图片的接收与交付 — 进度

## 总体状态

COMPLETED

## 授权与基线

- 需求、方案已确认；用户“可以”承接“方案确认，开始实现”的合并确认请求，实施授权有效。
- 分支 `feature/162-model-generated-image-delivery`，起点 `e928843650da8a0b23704b070343a4862749e553`。
- 2026-10-15 实现阶段仅执行确定性验证。
- 2026-10-16 用户明确授权一次真实验收：一轮生图、向原 QQ 私聊发送生成图片，以及真实桌面端验证。使用原 `my cpa / gpt-6-sol / medium` 连接的新短会话，避免历史压缩额外调用；不重复生图。原 QQ 绑定摘要 `af061ab1cde8`。

## 任务列表

- [x] Responses 图片输出事件、校验和纯图响应。
- [x] Agent 保存、有序消息、失败与取消、同轮与跨轮历史。
- [x] 会话文件发现、来源、持久恢复及附件 GC。
- [x] Bridge 编码、桌面图片块、查看和共享右键菜单文件定位。
- [x] QQ 分段图片投递、回执与失败反馈。
- [x] 自动化回归、项目检查与冻结运行时影响评估（standard；无新增 frozen 构建）。
- [x] 用户确认 QQ 图文正常后明确要求合入主干并推送；按已说明的验收边界交付。

## AC 验证

- [x] AC-1 接收/顺序/去重（合成 Responses transport）。
- [x] AC-2 桌面查看/右键定位已有自动化及图片预览证据；用户接受交付。真实右键视觉未执行，真实重启按用户决定延后，不记为实测通过。
- [x] AC-3 文件发现/历史/按需读取（隔离目录、rollout 与压缩后重建）。
- [x] AC-4 QQ 图文投递及部分失败（真实入站图文成功 + 先前纯图投递 + 确定性失败/未知测试）。
- [x] AC-5 缺图/损坏/超限/存储失败（确定性校验与故障注入）。
- [x] AC-6 中断/取消/未知结果/恢复边界（fake provider 与 SDK 回执）。
- [x] AC-7 诊断/兼容/项目门禁（全量套件及最终修正专项复验，范围见下）。
- [x] AC-8 授权验证已记录；用户确认图文收取正常并授权合入推送。

## 实现日志

| 日期 | 完成内容 | 验证或备注 |
|---|---|---|
| 2026-10-15 | 分支占号、需求与证据落盘、方案确认 | 未修改生产代码，开始实施 |
| 2026-10-15 | 实现 Responses → 原件保存 → 图文事件 → 桌面/QQ 交付 | 无真实 LLM 或 QQ 请求 |
| 2026-10-15 | 前端全量 lint/typecheck/test/build | 181 文件、1481 测试通过；后补保存按钮交互测试 1 项通过 |
| 2026-10-15 | Rust 格式、全部测试与 release check | lib 329 passed / 1 ignored，launcher 1、installer helper 7 passed |
| 2026-10-15 | 后端完整检查 | lint/typecheck 通过；全量回归进行中；发现 legacy 中断消息被新增 item_index 影响，已限定为原生有身份项并通过 runtime_dispatch 复验 |
| 2026-10-15 | 后端首轮完整回归及修复 | 4145 passed / 5 skipped / 1 failed（上述 legacy payload）；37 项专项复验通过，修复后全量门禁复跑中 |
| 2026-10-15 | 后端最终完整回归 | 4150 passed / 5 skipped；lint/typecheck 通过，退出码 0；QQ 来源绑定修正另经 46 项及静态门禁复验 |

## 确定性验证入口

- `scripts/test/run.ps1 llm_providers/tests/test_generated_images.py llm_providers/tests/test_responses_protocol.py scripts/package/test_package.py::test_frozen_image_capability_runs_all_static_decoders -q --tb=short`：86 passed。
- `scripts/test/run.ps1 agent/tests/test_generated_images.py agent/tests/test_runtime_dispatch.py agent_bridge/tests/test_session_bridge_reads.py -q --tb=short`：37 passed；随后增加 completed-image 中断保留参数，生成图片测试 5 passed。
- `scripts/test/run.ps1 agent_bridge/tests/test_im_router.py -q --tb=short`：46 passed，包含首图前流式文字、图片、图后文字、分段回执。
- `scripts/frontend/test.ps1 src/stores/assistantImages.test.ts src/stores/assistantPhase.test.ts`：9 passed。
- `scripts/frontend/test.ps1 src/pages/chat/components/GeneratedImage.test.ts`：1 passed，覆盖用户取消保存、保存成功和失败。
- `scripts/check/run.ps1 --stage frontend` 与 `--stage rust`：通过；后补前端测试后 lint/typecheck 再次通过。
- `scripts/check/run.ps1 --stage backend --tb=short -q`：4150 passed、5 skipped、8 warnings；完整阶段退出码 0。
- 全量复跑期间仅修正 QQ 前缀回执的来源消息绑定（图片先落盘不能代表文字来源）；
  `scripts/check/run.ps1 --stage backend agent_bridge/tests/test_im_router.py -q --tb=short`
  在该修正后通过全量 lint/typecheck 与 46 项路由测试。全量套件的源快照早于这一条来源绑定修正。

AC-2 的原生保存交互已按用户要求取消，改为共享右键菜单定位原件；旧版真实桌面缩略图/大图已观察，新右键交互仅自动化验证，真实重启延后到用户实际使用。
AC-4 已有确定性失败/未知测试；真实 QQ 单图投递与完整入站图文路由均已完成，用户确认图片和说明都收到且顺序正常（E-014）。

## 打包影响评估

本次为 `standard`：没有改依赖、锁文件、资源契约、动态导入或原生库加载。
新增 Python 模块及 QQ SDK 媒体子模块由既有 spec 的 collect_all 覆盖。
图片解码/校验/原件导入完全复用 ImageReadingService.snapshot；既有
`runtime_entry._smoke_image_reading` 已实际覆盖 PNG/JPEG/WebP 解码和原件往返。
该 smoke 的源码环境回归已通过；没有构建新的 frozen runtime，也不将源码测试称为冻结验证。
2026-10-16 已移除未发布的 Tauri 专用保存命令及路径 helper；改用现有文件定位接口和原生 reveal_link_target，无 Rust 新增差异、依赖或打包变化。

## 验收边界

2026-10-16 真实验收部分完成，证据详见 evidence-index 的 E-010–E-012：

- 原连接 my cpa / gpt-6-sol / medium，新短会话只提交一次生图；磁盘仅 1 条 rollout_request_prepared，回合 completed，保存 1 张有效 PNG 与 1 段文字。
- 当前工作区 Tauri 实例已实际显示缩略图、说明文字和大图。原生保存触发后电脑操作工具报告 Esc 中止；未确认目标文件保存成功。
- 用户随后明确要求不控制屏幕；此后仅使用独立 Python 进程和 HTTP/QQ API，没有窗口、键鼠或 DOM 操作，没有再次生图。
- 独立进程通过 SessionBridge.generated_image_path 恢复原会话图片；HTTP 原件下载与内容寻址原件逐字节一致。此项不冒充原生保存或完整桌面重启验收。
- QQAdapter 实际上传并发送同一张图片一次，平台返回 delivered，用户确认“就只收到单张图片”。本次有意只发图片，不含说明文字；未运行 QQ 入站路由或完整图文链路。
- 为遵守不干扰屏幕的约束，没有重启或关闭测试窗口；当时暂保留本任务 Tauri dev runtime；后续交互调整期间发现窗口已退出并完成后台清理，见本页收尾记录。

以上为第一轮观察时的历史状态；后续 QQ 完整链路、交互修订和用户合入确认见下方记录。
测试输出有 LiteLLM 异步日志清理、重复 ZIP 测试夹具及 bilibili_api 退出清理警告，未导致门禁失败；
前端构建有既有 highlight/CSS 与 chunk 大小提示。没有把这些提示写成失败或零警告。
第一轮验收时未创建安装包、未安装或合入；本轮 Git 交付仍不构建安装包，已安装客户端不会因提交自动变更。

## 2026-10-16 交互调整

用户明确要求不要额外保存按钮或图片名，改为复用右键菜单打开所在文件夹；同意真实桌面重启验收留待实际使用。
按该指令同步 requirement/design。保留左键大图，通过同一个共享菜单提供“打开所在文件夹”和“复制完整路径”。
Bridge 扩展生成图片的会话引用校验；未新增菜单组件、文件打开命令、依赖或模型调用。
删除这次需求未发布的专用 Rust 保存实现及其入口，避免保留无调用方代码。
用户要求不控制屏幕继续有效：没有实际打开文件夹、没有桌面 UI 验收，也没有额外生图或 QQ 发送。

验证：前端专项 3 文件 / 14 项已通过；后端 `scripts/check/run.ps1 --stage backend agent_bridge/tests/test_link_targets.py -q --tb=short` 通过全量 lint/typecheck 与 11 项文件定位测试。测试文件的格式、Any 返回类型及前端测试组件写法反馈已修正；前端 `scripts/check/run.ps1 --stage frontend` 完整通过：lint/typecheck、182 文件 / 1484 项回归、production build。构建仅有既有 highlight CSS 与 chunk 大小提示。

运行态收尾：只读 status 发现本次开发窗口 owner 已退出；随后通过 scripts/dev/run.ps1 stop 停止本任务 dev lease PID 21872，没有重新启动桌面。最终 status 确认没有活动 dev lease，所有开发端口空闲；残留 desktop owner 记录明确为已退出进程，不代表仍在运行。

## 2026-10-16 QQ 完整链路验收续跑

用户要求重新打开 dev，并明确“没说关别关”；本任务后续保持开发运行态，不再自动收尾停止。
用户要求执行完整 QQ 图文链路验证，构成本次真实单轮生图与原私聊图文回复授权。
已启动 scripts/dev/run.ps1；与前次单独 adapter 发送区分，本轮要覆盖真实 QQ 入站、路由、生成、图片/文字分段及回执。
现有 smoke 入口仅注入假 InboundEvent，不用它冒充真实入站；待通道就绪后由用户从原 QQ 私聊发送一条带 AF162-QQ 标记的测试消息。
只读观测从原会话 JSONL 偏移 952632690 开始；不改变原 QQ 绑定、不启动竞争 WebSocket、不重复发送或额外调用生图。

启动恢复记录：首次启动被 build-storage 的旧 running 操作 ae050337c8e245a985862df010f51f6c 拦截。确认 owner 40796、旧 lease 21872、app 51016/59216 和目标路径进程均退出，dev status 无活动 lease 且端口空闲后，使用标准 reconcile --confirm-stopped 恢复该操作记录；不修改其它 worktree 操作，不删除数据。随后重新运行标准 dev 入口。

重新启动已就绪：Tauri dev lease PID 10344，app PID 45448 / processSessionId 45448-1792086283983；原 QQ provider active、绑定仍为原会话。已请用户发送一条真实 QQ 入站测试消息，后台准备核对回合与分段回执。按用户明确要求保留 dev，不再自动 stop。

### QQ 完整链路结果

用户最终发送简短请求，无需 AF162-QQ 标记；观测按原会话基线偏移和本次 run_id 定位。
本地时间 01:47:48 QQ 真实入站经过 adapter → runtime → router；run `08006660fbef4effa05bdc80186c1014`，
仅 1 次模型请求，01:48:35 completed。持久化 1 张 PNG 和 1 段说明，图片原件可通过 HTTP 读取并通过 SHA-256 校验。
01:48:39 图片 part 0 和文字 part 1 依次得到 delivered 回执，source_message_ref 分别对应同一回合的图片/文字事件。
用户确认“图片和说明都收到，顺序正常”。本次 AC-4 的真实成功路径已通过；没有重试生成、人工 adapter 补发或更换 QQ 绑定。
详见 evidence-index E-014 和本机私有观测文件。仍不把本轮运行作为桌面重启或右键菜单的真实视觉验收。

dev 已重新打开并保留：worktree 6ed4，feature/162-model-generated-image-delivery，Tauri；lease PID 10344，app PID 45448，bridge PID 9704。
用户未要求关闭前不执行 stop；本轮最后仅做只读运行态核对。

## 合入交付

2026-10-16 用户明确要求合入主干并推送。按已确认范围接受交付；桌面重启继续留待实际使用，右键菜单只保留自动化证据。合入前重新执行后端全量门禁，公开指南按 merge-main-and-push 流程复核，dev 保持运行。

### 最终门禁与指南

- 用户 2026-10-16 确认 QQ 图片和说明均收到且顺序正常，随后明确要求合入主干并推送，按已记录边界完成验收。真实桌面重启延期；新右键交互保留自动化证据，不声称做过实际文件管理器点击。
- 实现提交 `2f3fd6c5`；同步最新 `origin/main` 后提交 `38ab259c`，无冲突。主干新增的是诊断说明清空与测试启动超时余量，与图片链路无文件重叠。
- 合入前后端完整 `scripts/check/run.ps1 --stage backend -q --tb=short`：4151 passed / 5 skipped，lint/typecheck 通过，退出码 0。运行期间同步的主干 Python 差异只把一个测试子进程超时从 20 秒放宽为 60 秒，没有改变生产逻辑或断言；该测试在全量运行中也通过。
- 同步主干后 `scripts/check/run.ps1 --stage frontend`：182 文件 / 1489 tests，lint/typecheck、production build 全部通过。既有警告详见前文。
- 最终 `frontend/src-tauri` 相对 main 无差异，复用先前 Rust 门禁证据，不为等价恢复或 Git 合入重新打包。
- 公开指南以产品提交 `38ab259c27febb6a60ce7cad83e5946cf952a343` 为来源，更新能力概览、使用、隐私和排障四篇 reference。Skill validator 与项目 verify 通过；指南提交 `7c2849dc596ffacc2017aa51fc2b84a2eaefd975` 已推送。
- 指南 [r93](https://github.com/A1m233/agent-friend-guide/releases/tag/agent-friend-guide-r93) 已发布，immutable=true，ZIP/manifest 摘要已核对；兼容范围沿用当前 0.4.1 至 0.5.0（不含）。知识按能力存在与否限定，不能为旧安装版本增加功能。未修改产品 builtin 快照。
- 本次收尾只更新需求状态和证据，产品代码与指南已审阅提交等价；复核无新增用户事实，不重复发布指南。
- dev 保持当前 feature worktree 运行；主干合并在独立 main checkout 执行，不切换或关闭用户正在使用的开发实例。
