# Agent Skill 系统 - 进度

## 总体状态

COMPLETED

## 任务列表

- [x] 建立 Skill 领域模型、路径、loader、builtin 资源与 catalog
- [x] 建立 catalog service、watcher、Bridge 生命周期与只读 catalog API
- [x] 接入 AG-UI 与 OpenAI-compatible 的结构化 Skill 选择协议
- [x] 接入会话事件、Conversation 首轮装配、持久化与幂等语义
- [x] 修正上下文轮边界、FIFO、摘要、失败清理与 edit-resend 投影
- [x] 补齐打包资源、确定性测试和项目完整检查门禁
- [x] 解析 `allow_implicit_invocation` 并更新 model-visible catalog 使用协议
- [x] 增加 `skill_read`、共享 snapshot activation resolver 与真实失败状态
- [x] 接入 Skill 工具结果的历史、摘要、缓存和调用方预选状态语义
- [x] 在桌面对话实时与历史投影中显示紧凑 Skill 状态
- [x] 建立 C20 真实 bad case、隔离项、controls 与 deterministic judge
- [x] 完成定向测试、项目门禁、获准后的真实 C20 和无键鼠桌面验收
- [x] 用户确认验收，需求状态转为 `COMPLETED`
- [x] 合入主干、推送远端并构建/安装/启动本地测试包

## AC 验证

- [x] AC-1 三类 Skill 发现
- [x] AC-2 builtin `skill-creator`
- [x] AC-3 无效 Skill 隔离
- [x] AC-4 同名与链接处理
- [x] AC-5 有界 catalog 与入口一致性
- [x] AC-6 结构化激活与渐进式加载
- [x] AC-7 Agent 语义选择与激活真实性
- [x] AC-8 上下文顺序与跨轮语义
- [x] AC-9 历史与再次激活
- [x] AC-10 新增热更新
- [x] AC-11 修改与删除热更新
- [x] AC-12 执行中 snapshot 稳定
- [x] AC-13 文件与脚本边界真实
- [x] AC-14 既有会话能力回归
- [x] AC-15 自动化门禁

## 实现日志

| 日期 | 完成内容 | 验证或备注 |
| --- | --- | --- |
| 2026-08-06 | 获得独立实现授权，进入阶段 3；建立实现与 AC 跟踪清单。 | 当前分支 `feature/076-agent-skill-system`；尚未执行代码修改。 |
| 2026-08-06 | 完成 Skill loader、builtin `skill-creator`、有界 catalog、热更新 service/watcher 与 `/v1/skills`。 | loader/catalog/service 定向测试已通过；watcher 覆盖 add/remove、shared root 晚创建。 |
| 2026-08-06 | 完成结构化选择、会话事件、首轮上下文顺序、重启恢复、幂等与 edit-resend 失活语义。 | Conversation Skill 测试 6 项通过；既有 Bridge 入口回归 31 项通过。 |
| 2026-08-06 | 完成 AG-UI 与 OpenAI-compatible 入口集成验证。 | 两协议 decoder/去重/拒绝隐式形态测试 5 项、OpenAI 路由集成与相关回归 14 项、AG-UI 路由集成 1 项通过。 |
| 2026-08-06 | 补齐热修改、轮内 snapshot、stale version、资源与脚本边界及 wheel 资源验证。 | Skill/上下文定向回归 133 通过、1 条 Windows 符号链接权限条件跳过；wheel 内确认 10 个 builtin resource entries。 |
| 2026-08-06 | 完成项目完整门禁，等待用户验收。 | 后端全量按 testpaths 有界分片：1534 通过、3 条平台/环境条件跳过；最终 Agent 重跑 730 通过、1 跳过，Skill Bridge 定向 16 通过；Ruff、mypy 471 files、前端 495 tests + production build、Rust 156 tests（1 ignored）+ release check 全通过。完整 backend/rust 首次串行调用都因 120 秒工具窗口在冷运行时超时，随后按仓库支持的有界分片或增量同命令取得正式通过结论。 |
| 2026-08-06 | 用户确认验收并授权一路推进到合入主干、推送远端及构建可信本地测试包；需求交付状态改为 `COMPLETED`。 | 全部任务、AC 与项目门禁证据齐备。 |
| 2026-08-06 | 同步最新主干后补齐冻结运行时打包契约。 | `watchfiles` 纳入直接依赖审查；frozen doctor 在隔离目录验证 builtin `skill-creator` package data、真实 watcher 热更新与按版本激活，不访问用户数据、网络或 LLM。package 定向 38 项通过；合入前完整门禁复验：后端 1551 通过、3 跳过，前端 495 项与 production build 通过，Rust 165 项（1 ignored）与 release check 通过。 |
| 2026-08-07 | 用户纠正原激活边界并重新打开需求：Agent 必须基于 catalog 对点名、序号、上下文指代和隐式匹配做语义选择，再通过结构化入口真实激活。 | `requirement.md` 已重新确认；原技术方案改回 `DRAFT`，AC-6、AC-7、AC-15 重新打开，等待阶段 2 方案确认后重做实现。 |
| 2026-08-07 | 用户确认 Codex 对齐方案并明确授权开始实现。 | 进入阶段 3；真实 C20 模型调用仍按 `llm-api-confirm` 在执行前单独取得授权，桌面验收仅使用不抢键鼠的 CDP/截图路径。 |
| 2026-08-07 | 完成 Agent 语义选择执行面：catalog 使用协议、`allow_implicit_invocation`、`skill_read`、轮内冻结 snapshot 和成功/失败状态。 | Skill/Conversation/AG-UI 定向 29 通过、1 条平台条件跳过；后端不解析用户 query，模型通过 catalog 与工具 schema 选择。 |
| 2026-08-07 | 完成调用方与模型两条激活路径的实时/历史紧凑 Skill 卡片。 | 前端 reducer 与 session projection 定向 30 项通过；卡片不渲染完整 `SKILL.md`。 |
| 2026-08-07 | 新增 C20 Skill 语义激活真实性契约 8 项，并扩展 case-local Skill fixture 与 deterministic judge。 | agent_eval loader/judge/runner 69 项通过；真实模型尚未运行，等待按逐次授权规则执行。 |
| 2026-08-07 | 完成项目三阶段正式门禁。 | 后端 Ruff + mypy 473 files + pytest 1573 通过/3 跳过；前端 498 tests + production build；Rust 165 tests/1 ignored + release check。 |
| 2026-08-07 | 按用户授权尝试无键鼠真实桌面验收。 | `desktop-visual doctor` 能力就绪，但现有已安装 runtime 占用 18800/18900，且不属于当前 worktree；安全守卫拒绝启动并未终止该进程。当前桌面视觉状态为 `not verified`，需用户允许停止/替换现有 runtime 后重试。 |
| 2026-08-07 | 按逐次授权完成 C20 真实模型运行。 | 原始报告 `agent_eval/reports/2026-08-07T15-23-04-34b2e91-C20.json` 留存 8 条完整 trace；首次旧 judge 为 4/8。逐条复核确认四项均为评测口径误伤：`named`/`referenced` 遥测边界过严、隐式策略拒绝被当作“调用即失败”、否定句“没能成功加载”命中成功子串。未再次调用模型；校准后的 judge 由 71 项确定性测试覆盖，并对同一批原始 trace 离线重判 8/8。 |
| 2026-08-07 | 按用户授权停止并替换已安装 runtime，完成无键鼠桌面验收。 | 精确关闭 `D:\agent-friend\agent-friend.exe` 及残留 runtime/connector 子进程；随后以隔离 `AGENT_FRIEND_DATA_DIR` 启动当前 `feature/076-agent-skill-system` Tauri runtime。通过 CDP/DOM 与截图确认成功卡片为绿色“已加载”、失败卡片为红色“加载失败”，名称、来源和失败原因可见，完整 Skill 正文标记不出现在 UI。证据位于 `C:\Users\example-user\.codex\visualizations\2026\08\05\019fd24a-ac2f-70e0-a7d4-dd441a32b294\076-skill-status\skill-status.png`、`dom.json`、`chat.html`；验收后 dev runtime 已停止且端口已释放。 |
| 2026-08-07 | 完成实现后最终三阶段项目门禁。 | backend：Ruff、mypy 473 files、pytest 1575 通过/3 跳过；frontend：eslint/token guards/typecheck、498 tests、production build；Rust：165 tests/1 ignored、release check。 |
| 2026-08-08 | 用户确认验收通过，需求状态转为 `COMPLETED`。 | 进入提交、合入 `main`、推送远端与本地测试包交付。 |
| 2026-08-08 | 完成主干与安装态交付。 | feature commit `08112fd`、merge commit `ab94679` 已推送；基于干净 `ab94679` 构建 `0.1.7` trusted-beta sensitive local-test，build ID `20260807T160900Z-ab946797fe88-27174496`，installer SHA-256 `aeb4c31f38cd32a0d4754e9857f05487ef979d3683d516c857a7ed752522b36a`。安装态 frozen runtime、builtin Skill 热更新、ONNX/sqlite-vec、bridge/voice readiness 与必需凭据检查均通过，应用已安装并启动。 |
| 2026-08-19 | 对齐 Codex 修正 Skill 调用信任边界：宿主解析结构化选择、`$skill-name`、带使用动作的精确名称和当前列表序号；模型 `skill_read` 只允许隐式调用，`allow_implicit_invocation=false` 的 Skill 不进入隐式 catalog。 | 定向回归 126 通过；完整 `agent_eval` 107 通过；Agent/Bridge 回归 886 通过、2 跳过；Ruff 与 mypy 520 files 通过。真实复验 C20-01、C20-05、C20-06 均通过，报告分别为 `2026-08-18T17-32-27-7ec2e46-C20-01.json`、`2026-08-18T17-32-50-7ec2e46-C20-05.json`、`2026-08-18T17-34-03-7ec2e46-C20-06.json`。C20-05 人工复核另观察到不可用审批入口被重复请求 5 次后由 `no_progress` 截停；该现象不影响 Skill policy 结论，未在本修复中扩张处理。 |
