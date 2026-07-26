# 066 · 附近地点搜索 - 进度

## 总体状态

COMPLETED

## 任务列表

- [x] 完成需求分析并确认 `requirement.md`
- [x] 完成开源/provider 调研并确认 `design.md`
- [x] 检查 065 远程状态和当前实现基线
- [x] 实现附近地点领域类型、错误模型与高德 V5 provider
- [x] 实现 `nearby_place_search` 工具、锚点澄清、排序和证据边界
- [x] 补 provider 与工具单元测试
- [x] 接入当前基线的 tool factory 与 HTTP 依赖
- [x] 贯通 runtime credentials、Bridge、Tauri 和设置页
- [x] 补工厂、凭据、Bridge、Rust/前端和 Conversation 事件链集成测试
- [x] 运行受影响测试与项目三阶段 `scripts/check`
- [x] 065 合入后基于最新 `main` rebase，解决共享接入点冲突并复验
- [x] 统一所有凭据及模型设置的运行服务自动刷新语义
- [x] 补保存、删除、Bridge / Voice Bridge 与部分成功状态测试
- [x] 重跑受影响验证与项目三阶段 `scripts/check`
- [x] 逐项核对 AC 并完成用户验收

## 实现日志

| 日期 | 完成内容 | 备注 |
|---|---|---|
| 2026-07-26 | Phase 1/2 完成；需求与技术方案均已确认 | 默认 5 公里；成功结果披露锚点和半径；允许与 `web_search` 分步协作 |
| 2026-07-26 | 检查 `origin/main` 与 `origin/feature/065-trustworthy-read-tools-and-tool-runtime` | 两者当前同为 `e0a8e28`，065 尚无远程实现提交；先推进隔离核心，共享接入前再次同步 |
| 2026-07-26 | 完成高德 V5 provider 与 `nearby_place_search` 工具核心 | 覆盖保守锚点澄清、失效引用续查、默认 5 公里范围披露、距离排序、字段过滤和稳定错误分类 |
| 2026-07-26 | 完成 provider / 工具定向验证 | `27 passed`；`scripts/lint` 全部通过；全部 HTTP 使用 mock transport，未调用真实高德服务 |
| 2026-07-26 | 贯通 runtime config、Bridge、tool factory、Tauri registry 与设置页 | `AMAP_WEBSERVICE_KEY` 复用现有 secret 管理；无 Key 时工具仍可见并返回可恢复配置提示 |
| 2026-07-26 | 验证现有 Conversation 工具事件链 | fake model + 真实 `NearbyPlaceSearchTool` 覆盖请求、结果落盘、第二轮回灌和明确范围表述；未修改 Conversation |
| 2026-07-26 | 项目三阶段自动化门禁通过 | backend：`1092 passed, 2 skipped`；frontend：`435 passed` + TS/build；Rust：`140 passed, 1 ignored` |
| 2026-07-26 | 再次检查 065 集成基线 | 远程 065 仍与 `origin/main` 同为 `e0a8e28`；065 工作区存在大量未提交实现，不能安全 rebase，等待其形成提交 |
| 2026-07-27 | 完成真实桌面视觉验收 | 首次受安装版占用阻塞；用户退出安装版后复验通过。真实 Tauri WebView2 CDP 确认“地图与地点”卡片布局、未配置状态、password 输入、provider 锁定及按钮禁用状态均正确 |
| 2026-07-27 | 基于已合入 065 的最新 `origin/main@f0d2f86` 完成集成 | 保留 065 tool runtime、calculate、datetime、web_fetch 及 Conversation 循环；附近地点工具接入为 30 秒预算的 `parallel_read`，未新增并发或超时机制 |
| 2026-07-27 | 065 集成后三阶段门禁通过 | backend：`1140 passed, 2 skipped`；frontend：`435 passed` + TS/build；Rust：`139 passed, 1 ignored` + release check |
| 2026-07-27 | 用户确认统一运行时配置自动生效范围 | 所有 registry 凭据按 effect 自动刷新对应服务；对话/记忆模型自动刷新 Bridge；持久化成功与刷新失败分离，活动交互不被强行中断 |
| 2026-07-27 | 完成统一 runtime config effect 执行 | secret 保存/删除和模型设置共用串行的“持久化 → 按 effect 重启”流程；环境变量覆盖和服务拒绝重启均呈现“已保存但尚未应用” |
| 2026-07-27 | 修正 supervisor 拒绝停止后的状态恢复 | 活动任务/通话或控制请求失败时保留原进程 `Ready`，错误进入 `last_error`，后续请求不会因仍占用端口而误判服务损坏 |
| 2026-07-27 | 统一配置生效改动通过完整门禁 | backend：`1140 passed, 2 skipped` + lint/typecheck；frontend：`442 passed` + lint/typecheck/build；Rust：`140 passed, 1 ignored` + release check |
| 2026-07-27 | 完成真实桌面复验并刷新当前 Bridge | 真实 Tauri WebView2 CDP 确认全部凭据显示自动重启语义，高德 Key 为 keyring 已配置；通过 supervisor 重启 Bridge 成功，返回 `ready`、generation 3 |
| 2026-07-27 | 完成原案例真实 LLM + 高德两轮回放 | 会话 `e4a5774a-e7b2-40d0-b93e-754de6b81c82`：首轮正确调用 `nearby_place_search` 并对深圳多个福民支行请求澄清；模拟选择招商银行深圳福民支行后原样复用 `anchor_ref`，在 2 公里内按距离返回 5 个地点并生成范围透明的最终回答；未调用 `web_search` 伪造附近性 |
| 2026-07-27 | 根据首轮真实回放收紧地点回答证据边界 | tool description 与结果 reminder 要求逐项保留可定位地址、把营业时间表述为地点服务标注而非实时状态，并在候选截断时暴露剩余数量；新增截断候选和完整地址回归断言 |
| 2026-07-27 | 完成收紧后的第二次真实两轮回放 | 会话 `0c8df8a6-be24-4cbd-b4e9-16f64f78dfa7`：最终回答保留楼栋/房号，营业时间改为地点服务标注并建议电话确认；同城与锚点已明确时省略重复行政区前缀，仍保留可定位地址。澄清回答未复述 `additional_candidate_count=3`，但仍展示可区分候选并等待确认，不影响禁止静默猜测的核心边界 |
| 2026-07-27 | 回放收紧后 backend 完整门禁通过 | `1141 passed, 2 skipped`，ruff 与 366 source mypy 全部通过；frontend/Rust 未受本轮 prompt/result 文本改动影响，沿用同日完整门禁结果 |
| 2026-07-27 | 用户确认验收并授权合入主干 | 逐项验收证据齐备，总体状态更新为 `COMPLETED` |
| 2026-07-27 | 合入前最终三阶段门禁通过 | backend：`1141 passed, 2 skipped` + lint/typecheck；frontend：`443 passed` + lint/typecheck/build；Rust：`140 passed, 1 ignored` + release check |

## 验收证据

| AC | 自动化证据 | 状态 |
|---|---|---|
| AC-1 / AC-2 / AC-2a | 工具测试覆盖距离/相关性模式、可靠最小字段、默认与自定义半径；Conversation 集成测试覆盖“福民银行最近照相馆”和最终范围表述；真实回放以确认后的招商银行深圳福民支行为中心，在 2 公里内按距离返回 36–191 米的 5 个地点 | 自动化与真实回放通过 |
| AC-3 / AC-4 | 多候选与同城多候选均返回 `needs_clarification`，只接受原样 `anchor_ref`；真实回放首轮展示四个可区分银行候选并等待用户选择，续轮原样复用所选引用 | 自动化与真实回放通过 |
| AC-5 | provider 缺失电话/营业时间时结果字段省略；真实复验把存在的营业时间明确表述为地点服务标注，并提示出发前确认，不推断实时营业状态 | 自动化与真实回放通过 |
| AC-6 | 无锚点、无 POI、缺 Key、鉴权、额度、限流、参数及临时失败均有稳定自然兜底；tool description 禁止网页伪降级 | 自动化通过 |
| AC-7 / AC-8 | tool description 固定地图/网页事实边界，schema 只接受用户锚点且不含设备位置入口；真实回放只用地点数据回答距离事实，没有回退 `web_search` | 自动化与真实回放通过 |
| AC-9 / AC-9a / AC-9b / AC-9c | runtime config Python/Rust/TS registry、Bridge 注入与设置页共用现有凭据入口；前端测试覆盖 secret 保存/删除、Bridge/Voice effect、模型设置、串行执行、env 覆盖和重启受阻；Rust 测试覆盖拒绝停止后保持服务可用 | 自动化通过；真实 Tauri Bridge 重启通过 |
| AC-10 | Conversation 集成测试覆盖通用工具请求、结果、持久化和回灌，未修改工具循环 | 自动化通过 |
| AC-11 | backend、frontend、Rust 三阶段门禁均通过 | 基于已合入 065 的最新 main 复验通过 |

用户已明确确认验收并授权合入主干，总体状态为 `COMPLETED`。

设置页视觉证据：`C:\Users\example-user\AppData\Local\Temp\agent-friend-066-settings-map-viewport.png`、`C:\Users\example-user\AppData\Local\Temp\agent-friend-066-settings-map-section.png`、`C:\Users\example-user\AppData\Local\Temp\agent-friend-066-runtime-auto-restart.png` 和 `C:\Users\example-user\AppData\Local\Temp\agent-friend-066-amap-auto-restart.png`；控件状态通过同一真实 WebView 的 CDP DOM 查询核验。
