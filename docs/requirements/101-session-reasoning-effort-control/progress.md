# 101 · 会话推理强度控制 - 进度

## 总体状态

COMPLETED

桌面视觉与交互、provider replay、Vision 参数门禁和 QQ 尾随菜单均已实现并通过自动化门禁。QQ 主菜单保持两排，第一排为“切换模型 / 调整推理强度”，第二排为“切换会话”。用户已确认调整完成后可合入主干并进入正式包交付。

## 任务列表

- [x] 扩展统一模型目录，声明 reasoning control、preset、默认值与 replay scope。
- [x] 将产品 preset 映射为 DeepSeek、Kimi、OpenRouter 的合法请求参数。
- [x] 增加会话级原子 `model + reasoningPreset` 配置、兼容旧事件并恢复历史会话。
- [x] 接入 REST、AG-UI、新会话 pending 配置、乱序保护与 run 内配置快照。
- [x] 捕获并按 provider 生命周期回放 opaque reasoning continuation。
- [x] 隔离聊天投影、REST、诊断包与日志中的原始 reasoning 内容。
- [x] 落地单入口模型 / 推理强度级联菜单与保持打开交互。
- [x] 同步诊断包 machine contract、消费者 Skill、格式说明与 adapter 校验。
- [x] 补齐后端、前端、Rust 自动化与真实 Tauri 验收。
- [x] 修正 DeepSeek V4 Flash Vision reasoning 目录与 provider 参数测试。
- [x] 修正级联子菜单间距、non-modal 外部交互和菜单内成功反馈。
- [x] 重跑受影响测试、完整门禁与真实 Tauri/CDP 验收。
- [x] 恢复独立 Sonner success toast，移除 Root 内联成功反馈并重新验收关闭按钮。
- [x] 为 DeepSeek Vision 的 OpenAI-compatible 路由窄范围放行 `reasoning_effort`，并重跑真实工具循环 replay smoke。
- [x] 扩展 QQ 尾随菜单，展示并调整当前绑定会话的推理强度。

## AC 验证

- [x] AC-1～AC-3：源码新鲜的真实 Tauri + bridge 返回 Vision effort 目录；新对话临时态完成 Flash → Vision、Vision `off → high → off`，四次 `/v1/models/validate` 均返回 200 与预期完整 runtime config。
- [x] AC-4～AC-9：store、AG-UI decoder、REST、session projection 与前端并发测试覆盖 pending、恢复、旧会话、原子提交和迟到响应。
- [x] AC-10：既有 run snapshot 回归与本次完整类型/测试门禁覆盖 run 内冻结。
- [x] AC-11：provider catalog/request 自动化覆盖 DeepSeek Vision 默认 `off`、开启态 `high` 参数和 `conversation` replay scope；精确 Vision route 的 golden mock HTTP 证明 LiteLLM 白名单不会丢弃 provider `reasoning_effort`。
- [x] AC-12：真实 smoke 已确认 DeepSeek V4 Flash、DeepSeek V4 Flash Vision 与 Kimi K2.6 各完成 2 次模型调用、1 次工具调用并记录 `model_continuations=1`，均无 `RUN_ERROR`。
- [x] AC-13～AC-14：普通投影、REST 和诊断测试覆盖隐私；编辑重发沿用 run snapshot 机制并通过全量回归。
- [x] AC-15：真实 Tauri/CDP 验证 Sonner 位于 Root 外且无内联反馈，Root/Sub 水平间距 3.2px；页面 `pointer-events: auto`，Sonner X 为命中栈顶层并可关闭 toast。点击外部 Sonner X 同时按 non-modal outside interaction 关闭 Root。
- [x] AC-16：修订后完整门禁通过；最新结果见实现日志。
- [x] AC-17：IM control 测试覆盖主菜单模型/推理摘要、DeepSeek 四档选择、Luna 六档分页、模型默认联动、fixed 只读态、busy 锁及陈旧 session/model token 拒绝；router/QQ adapter 相邻回归通过。

## 实现日志

| 日期 | 完成内容 | 验证或备注 |
|---|---|---|
| 2026-08-23 | 完成模型能力目录、provider 映射、会话原子配置、REST/AG-UI 与旧会话 fallback | 定向后端回归 127 passed；全仓 mypy 通过 |
| 2026-08-23 | 完成 reasoning continuation 私有持久化、工具循环回放和 REST/诊断双重脱敏 | reasoning 集成、session projection、diagnostic contract 与 Rust archive 测试通过 |
| 2026-08-23 | 完成受控 Root/Sub 级联菜单；修复旧 Sub close 回调覆盖新 Sub、具体项选择时 Sub 自行关闭两个真实交互竞态 | 前端组件测试覆盖 pointer/focus/select/Escape；完整 frontend 607 passed |
| 2026-08-23 | 真实 Tauri 验收：Vision 显示“不支持”且无 radio；切到 Flash 后出现关闭/低/高/最高；选择“最高”后 Root/Sub 均保持；Escape 关闭 | `desktop DOM via CDP` + `desktop interaction`；截图保存在本机临时目录 `agent-friend-101-pair/webview2-cdp.png`，未纳入仓库以避免会话内容泄露；测试会话已恢复验收前配置 |
| 2026-08-23 | 修正 session list 只读首行导致 model/preset 摘要陈旧的问题，改为反向投影最新原子配置并输出 `reasoningPreset` | store/API 新增回归；定向 46 passed |
| 2026-08-23 | 完整项目门禁 | backend 2135 passed / 4 skipped；frontend 106 files / 607 tests + production build；Rust 215 passed / 1 ignored + release check |
| 2026-08-23 | 用户桌面复验发现三处缺口并确认修订方案 | Vision 改为显式 effort；SubContent 使用局部 sideOffset；Root 改为 non-modal，成功反馈移入菜单且保留 aria-live；当前重新实现与验收 |
| 2026-08-23 | 完成三处增量修正及定向回归 | Vision 显式复用 DeepSeek reasoning；两个 SubContent 使用 `sideOffset=8`；Root non-modal，成功反馈改为菜单内短暂状态；backend 28 passed，frontend 10 passed |
| 2026-08-23 | 修订后完整项目门禁 | backend 2135 passed / 4 skipped；frontend 106 files / 608 tests + production build；Rust app 215 passed / 1 ignored、installer helper 6 passed + release check |
| 2026-08-23 | 修订后真实 Tauri WebView UI 验收 | `desktop DOM via CDP` + `desktop interaction`；Vision 四档可见，SubContent 间隔 3.2px，成功反馈位于菜单内，Sonner X 可命中并关闭；为避免重启既有运行态，catalog/validate 使用临时 CDP 注入，后端契约由全量门禁独立覆盖；截图 `C:\Users\example-user\AppData\Local\Temp\agent-friend-101-reverify-menu.png` 未纳入仓库 |
| 2026-08-23 | 用户授权重启后完成源码新鲜的真实 Tauri E2E | 同 worktree/feature 分支的 Tauri、bridge、voice 均在改动后启动；真实目录返回 Vision 四档，四次本地 validate 均 200，菜单间隔/反馈/Sonner 命中全部通过；截图 `C:\Users\example-user\AppData\Local\Temp\agent-friend-101-fresh-e2e.png` 未纳入仓库 |
| 2026-08-23 | 用户纠正成功反馈形态并确认改回 | 保留独立 Sonner success toast；删除 Root 内联状态；继续使用 non-modal 修复 toast X/action 的可点击性；当前重新实现与验收 |
| 2026-08-23 | 恢复独立 Sonner 并完成复验 | frontend 106 files / 608 tests + production build；源码新鲜的真实 Tauri/CDP 验证 toast 不在 Root 内、无旧内联状态、Root 高 73.6px、Sub 间距 3.2px、页面可交互且 X 为顶部命中并成功关闭；截图 `C:\Users\example-user\AppData\Local\Temp\agent-friend-101-sonner-restored.png` 未纳入仓库 |
| 2026-08-23 | DeepSeek V4 Flash 与 Kimi K2.6 真实 reasoning replay smoke | 两组均由 `calculate(17*23)` 触发工具循环并完成 2 次真实模型调用；`model_continuations=1`、最终回复 `SMOKE_OK 391`、无 `RUN_ERROR`；公开 session API 仅返回脱敏 continuation 长度/数量 |
| 2026-08-23 | Vision 真实 smoke 暴露运行时缺口 | `deepseek/deepseek-v4-flash-vision-exp` + `high` 首请求在 LiteLLM 本地参数校验被拒绝；provider 未收到请求、工具与 continuation 均未发生。根因是 OpenAI-compatible route 默认不允许 `reasoning_effort`；待窄范围放行后复验，不使用会静默丢参的 `drop_params` |
| 2026-08-23 | 修复 Vision LiteLLM 参数门禁并完成真实复验 | 精确 Vision route 仅在存在 `reasoning_effort` 时增加 `allowed_openai_params`；定向 23 passed，backend lint/mypy + 2135 passed / 4 skipped；fresh bridge PID `39544` 下 Vision `high` 完成 2 次模型调用、1 次工具调用、`model_continuations=1`，最终 `SMOKE_OK 391` 且无 `RUN_ERROR` |
| 2026-08-23 | QQ 尾随菜单增加推理强度 | 主菜单展示 catalog label；新增 preset 分页与 fixed/unsupported 只读页；模型/推理都走 `SessionModelService.update()` 并回显最终配置；token 绑定生成时 session/model 防止陈旧菜单误改；定向 110 passed，backend lint/mypy + 2141 passed / 4 skipped |
| 2026-08-23 | 重启并加载 QQ control 新实现 | fresh Tauri PID `7948`、bridge PID `9552`；同 worktree/feature 分支，QQ provider 成功恢复 WebSocket session；未主动向真实 QQ 发送测试消息，等待用户从下一条回复尾随菜单手验 |
| 2026-08-23 | 按用户手验调整 QQ 两排菜单顺序 | 第一排“切换模型 / 调整推理强度”，第二排“切换会话”；布局行数不变，IM control/router/QQ adapter 定向 73 passed |
| 2026-08-23 | QQ 菜单顺序调整后的最终后端门禁 | Ruff 通过；mypy 570 source files 通过；pytest 2141 passed / 4 skipped；仅保留既有 bilibili atexit event-loop warning |
| 2026-08-23 | 用户确认需求验收完成并授权合入主干、推送远端及构建正式包 | 进入发布收口流程；正式版本号需使用未发布的新 SemVer，不能覆盖现有 `0.2.1` |

## 验收待办

- [x] 用户确认本需求验收完成。
