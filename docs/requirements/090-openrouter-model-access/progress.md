# OpenRouter 模型渠道接入与调用层解耦 - 进度

## 总体状态

COMPLETED

## 任务列表

- [x] M1：新增 LanguageModel 契约、请求/响应/控制类型，并拆分 usage 与错误归一化职责。
- [x] M2：迁移 Agent、Context、Memory、SessionManager、Bridge 与测试 fake，移除具体 LLMClient/ProviderSpec 上层依赖。
- [x] M3：新增 OpenRouter 产品目录、LiteLLM 路由策略、provider-aware 错误和 mock 调用测试。
- [x] M4：扩展 Python/Tauri/前端 OpenRouter credential group 与 `/api/v1/key` 校验。
- [x] M5：完成模型设置/会话选择前置检查、调用渠道文案和精确凭据设置跳转。
- [x] M6：运行局部测试、全量 check、package-sensitive frozen runtime 验证并处理回归。
- [x] M7：按用户授权完成真实 Tauri 桌面视觉、DOM、交互与持久化交叉验收；真实 OpenRouter 模型调用未执行，也不作为本次视觉验收前提。

## AC 验证

- [x] AC-1～AC-3：目录、调用渠道交互与缺凭据前置检查由 catalog、设置页和 conversation store/selector 测试覆盖；选择失败发生在持久化前，并携带精确 credential group。
- [x] AC-4～AC-5：OpenRouter 使用 `GET /api/v1/key` 校验，仅读取 HTTP 状态；候选凭据事务、旧值保护、单次应用与 Bridge 重启继续复用需求 089。
- [x] AC-6～AC-9：mock 请求固定目标模型、工具参数、`require_parameters=true` 与 `data_collection=deny`，覆盖流中错误归一化和 text-only 能力声明；未配置 fallback。
- [x] AC-10～AC-12：Agent、Context、Memory、SessionManager、Bridge 和 eval 面向 LanguageModel/Factory；独立 fake 直接实现协议，既有 Agent 语义回归通过。
- [x] AC-13～AC-14：DeepSeek/Kimi 既有测试与全量门禁通过；OpenRouter 自动化全部使用 mock/fake，未调用真实第三方 API。
- [x] AC-15：Python、frontend、Rust 全量门禁及 package-sensitive frozen runtime 验证通过；真实 OpenRouter 调用不属于本次自动化验证，若后续执行将单独授权并记录。

## 实现日志

| 日期 | 完成内容 | 验证或备注 |
| --- | --- | --- |
| 2026-08-20 | 从 `origin/main@c6aba5c` 创建并占用 `feature/090-openrouter-model-access`。 | 基线包含已合入的需求 089。 |
| 2026-08-20 | 确认 requirement、design 与 evidence index，开始实现。 | 自动化不调用真实第三方 LLM；真实调用另行授权。 |
| 2026-08-20 | 建立项目自有 `LanguageModel` / `ModelRequest` / `ModelResponse` / `ModelCallControl` 契约与工厂；把 usage、错误归一化和具体 LiteLLM client 拆为独立职责。 | 上层生产代码不再依赖 `ProviderSpec`，也不再通过具体客户端类型决定核心调用行为。 |
| 2026-08-20 | 迁移 Agent、Context、Memory、SessionManager、Bridge、eval、smoke 脚本与测试 fake。 | fake model 无需继承或强制转换为具体客户端；会话显式持有产品 `model_id`。 |
| 2026-08-20 | 新增 OpenRouter 固定目录、路由约束、凭据生命周期、错误模型和前端选择前置检查。 | 只开放 Gemini 3.7 Flash 与 GPT-5.6 Luna；不开放自动路由、fallback 或多模态输入。 |
| 2026-08-20 | 完成项目统一自动化门禁。 | Python：1932 passed / 4 skipped，ruff 与 mypy 通过；frontend：573 tests、lint、typecheck 与 production build 通过；Rust：lib 204 passed / 1 ignored、helper 6 passed、release check 通过。 |
| 2026-08-20 | 完成 `runtime --local-test --verification-level package-sensitive`。 | build `20260820T064625Z-c6aba5cd9224-dc53ecb9`，PyInstaller 与 frozen runtime doctor 全部通过；过程中的第三方 hook warning 未影响结果。 |
| 2026-08-20 | 自动化实现与验证收口。 | 未发起真实 OpenRouter/其它 LLM 请求；等待确认是否执行真实 Tauri 桌面视觉验收。 |
| 2026-08-20 | 用户授权后完成真实 Tauri 桌面验收。 | 运行实例绑定当前 worktree / `feature/090-openrouter-model-access` / Tauri / CDP 9222；通过真实 WebView2 screenshot、DOM 与交互确认“调用渠道”、OpenRouter 凭据组、两款模型可见、缺凭据拦截和精确设置跳转。 |
| 2026-08-20 | 交叉核对缺凭据拦截的持久化结果。 | 设置页与会话页尝试选择 OpenRouter 后仍保持 `kimi/kimi-k2.5`；桌面 `settings.json` 中 chat/memory model 字段未变化。未填写、保存或读取任何 OpenRouter Key，未调用真实 LLM。 |
| 2026-08-20 | 用户确认验收并要求合入主干、推送远端。 | 进入 `merge-main-and-push` 收口；先维护公开 guide 与 builtin，再合入 `main`。 |
| 2026-08-20 | 发布公开 OpenRouter 用户知识。 | 产品来源提交 `bb2675d7` 形成后，公开 guide 更新模型渠道、凭据、选择与排障说明；validator / verify 通过并发布为 `6a295874`，远端 `main` 已精确确认。 |
| 2026-08-20 | 同步产品 builtin 并完成最终门禁。 | builtin、manifest 与公开 guide snapshot 完全一致；Backend 1932 passed / 4 skipped，Frontend 573 tests 与 production build，Rust 204 passed / 1 ignored、helper 6 passed 与 release check 均通过。 |
