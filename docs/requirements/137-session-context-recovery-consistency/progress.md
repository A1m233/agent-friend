# 137 · 实施进度

## 总体状态

COMPLETED

## 授权与基线

- 用户确认需求，并授权按方案实现、修正有证据的架构问题、桌面验收及经 CPA 的真实 LLM 验证。
- 基线 `1ebee945baf86778a7b779740d3635701c6d5324`；分支 `feature/137-session-context-recovery-consistency` 已远程占号。
- 不修改用户现有会话，不执行 MAA 业务；真实调用逐次声明有界数量。

## 任务

- [x] 需求、证据及设计确认
- [x] 失败编辑 warm/cold 历史膨胀的离线复现
- [x] 计量兼容、工具轮 usage 恢复与压缩可行性
- [x] 用户输入接受及有效模型历史持久化/恢复
- [x] 共享错误持久化与受影响入口回归
- [x] 中断事实归属与旧提示兼容
- [x] 同类路径盘点及组合回归
- [x] scripts/check backend、frontend、rust
- [x] CPA 有界真实模型验证
- [x] 真实桌面验收与运行态清理
- [x] 用户最终验收

## AC 验证

| AC | 证据与结果 |
| --- | --- |
| 01 | 输入身份、v3 迁移、窗口/effort/route 回归；CPA low→medium 真实 usage_anchor，E-013/E-016 |
| 02 | mandatory-tail/outage/base-budget、候选最终校验和失败不提交摘要回归；真实桌面过大输入 0 对话调用 |
| 03 | 新旧失败编辑 warm/cold 保留已压缩前缀；provider reject/cancel/empty；CPA 重启后回复新标记 |
| 04–05 | 中断正文归属、位置、状态区分、重复恢复和失活 marker 回归；旧提示不会作为新任务的尾部指令 |
| 06 | Bridge 实际 factory 编辑失败 + reload，run_error 幂等；桌面编辑失败刷新后可见，E-014/E-017 |
| 07 | 沿用 runtime cancellation、tool recovery、权限和 rollout 回归；合成验收无业务工具动作 |
| 08 | 下表逐入口盘点；IM/语音/定时任务本次未做外部服务真实 E2E，不扩大结论 |
| 09 | 完整 Conversation/Bridge 入口、真实 CPA 与真实 WebView + 落盘交叉核对；保留原诊断中已证伪的局部推断 |
| 10 | frontend 157 文件/1310 tests + build 通过；Rust test + release check 通过；backend 全量 3374 passed / 5 skipped 通过，追加旧 v3 配置迁移矩阵所在文件 13 项通过；用户已确认验收并要求合入 main、推送远端、构建 local-test |

## 相邻路径盘点

| 入口 | 影响与覆盖 |
| --- | --- |
| 桌面普通发送/编辑重发/继续 | 共用 user turn 与 runtime；编辑遗漏由默认错误 callback 修复；普通/编辑真实桌面、继续和异常由 deterministic tests 覆盖 |
| IM | 同一 Conversation 与 Runtime；保留 IM transport/persist callback；IM router/runtime inbound/streaming 等既有 fake 回归，未发真实 IM 消息 |
| OpenAI/语音 | 同一 user turn，持久会话保留 OpenAI callback，暂态会话不新增用户历史错误；OpenAI routes 与 voice integration/unit 回归，未拨打真实通话 |
| 定时任务/提醒/社交授权跟进 | 原本未显式配置错误 callback；共享 runtime 默认回调补齐用户可见失败原因，原业务 watcher 不变；runner/delivery/social-auth 既有 fake + 默认回调 system-turn 验证 |
| memory_only / idle reflection | 保持静默，默认 callback 不制造聊天错误；silent-turn 与默认错误策略单测 |
| 冻结 runtime | standard：无依赖、资源装配、动态 import/原生加载变更；新 Python 模块均静态可达；未构建/安装/发布安装包 |

## 验证命令与运行态

- `scripts/check/run.ps1 --stage frontend`：lint/typecheck、1310 tests、production build 通过；既有 CSS highlight 与 chunk-size 警告保留。
- `scripts/check/run.ps1 --stage rust`：cargo fmt/test/check --release 通过；既有 dead_code 警告保留。
- 初轮 backend 全量 3375 项：3365 passed、5 skipped、5 failed。失败为持久化边界变化后的断言/迁移时机断言，已修正；`--lf` 5 项通过，受影响模块 122 项通过。最终 `scripts/check/run.ps1 --stage backend -q`：3374 passed、5 skipped、3 warnings，退出码 0；新增 effort/window/route 迁移矩阵的文件独立 13 项通过，随后 typecheck 通过。第三方 bilibili_api atexit 清理输出 Event loop is closed，但不影响测试退出码；作为既有清理噪声保留，不声称无警告。
- `context-recovery-smoke/run.ps1 --allow-llm --connection-name 'my cpa' --model <CPA luna ref> --output <new temp JSON>`：3 轮真实对话通过，未提交私有会话；第一次连接名与 preset 校验失败发生在模型调用前（0 请求），修正 harness 参数后才执行。
- 真实 Tauri lease：本 worktree、本 feature 分支、mode=tauri；应用 PID 5740，13:20:24Z 启动；Bridge 经 supervisor 于 13:29:51Z 重启，晚于所有产品源码变更。独立 DATA/LOG 临时目录，实际 WebView `http://localhost:1420/chat.html`。
- 桌面普通输入过大→错误，编辑仍过大→错误，reload 保留第二次错误，重启 Bridge 后 UI 编辑短输入→CPA 回复与 completed 落盘。实际 1 对话 + 1 通知 + 1 记忆提取，均 CPA；总调用数 6。
- 验收 dev runtime 已通过标准 stop 结束，status 已确认无活跃 lease、owner-process-exited、dev ports are free。证据为合成内容，原用户会话未改写。

## 实现日志

| 日期 | 工作 | 证据/备注 |
| --- | --- | --- |
| 2026-09-19 | 建档、官方源码对照、完整入口探针及同类路径盘点 | evidence-index E-001–E-012；尚未完成产品修复 |
| 2026-09-19 | 完成核心修复、相邻路径回归、CPA 和真实桌面验收 | E-013–E-018；全部自动化及已授权实测通过，用户于后续指令确认验收并授权合入 main / 推送 / local-test 构建 |

## 交付授权

2026-09-19：用户要求“合入主干推送远端，然后打个本地测试包”，确认本需求验收。开始执行 Guide 复核、保留 merge commit 合入、远端推送与 local-test 完整构建；不自动安装。
