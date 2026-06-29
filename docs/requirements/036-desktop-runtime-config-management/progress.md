# 036 · 桌面端运行时配置管理 - 实现进度

## 状态

COMPLETED

## 任务

- [x] 补齐 Python runtime credential registry，加入当前语音输入会用到的 `VOLC_SPEECH_API_KEY`
- [x] 扩展桌面 settings，加入 DeepSeek 主模型设置
- [x] 扩展桌面 settings / runtime_config，加入 DeepSeek 记忆模型设置并支持从 legacy env 迁移
- [x] 新增 Tauri runtime config facade，支持 secret 状态查询、写入、删除
- [x] 新增前端 runtime config typed client / hook
- [x] 抽出 DeepSeek 默认模型解析，并把新会话 requested model 带到 AG-UI 请求
- [x] 扩展 bridge decoder / route，仅允许两个 DeepSeek 模型用于新 session auto-create
- [x] 设置中心接入基础查看 / 修改 UI
- [x] 设置页抽出 page layout、secret field、secret list 等复用组件，保留后续视觉重做空间
- [x] 语音输入链路接入 `VOLC_SPEECH` runtime credentials，并修复失败后卡住的前端状态收敛
- [x] 保存后自动重启暂缓，登记 issue 031
- [x] 补充自动化测试并运行验证
- [x] 按项目视觉验收边界记录：本期 UI 是非最终雏形，不做正式截图硬门槛

## 日志

| 时间 | 记录 |
|------|------|
| 2026-06-29 | Phase 3 开始：按 design.md 实现查看 / 修改能力 |
| 2026-06-29 | 完成 runtime_config registry 补齐、Tauri keyring facade、前端 hook、DeepSeek 模型设置链路和设置页雏形 |
| 2026-06-29 | 验证通过：`cargo check --manifest-path frontend/src-tauri/Cargo.toml`、`cargo test --manifest-path frontend/src-tauri/Cargo.toml runtime_config`、`uv run pytest runtime_config/tests/test_sources.py agent_bridge/tests/test_ag_ui_decoders.py`、`pnpm --dir frontend typecheck`、`pnpm --dir frontend lint`、`pnpm --dir frontend lint:colors`、`pnpm --dir frontend lint:tokens`、`pnpm --dir frontend lint:ui-provenance`、`pnpm --dir frontend test` |
| 2026-06-29 | 桌面视觉检查尝试：`desktop-visual doctor` 通过；真实启动被审批拦截，因为脚本可能自动清理 1420 端口旧进程。已确认 1420 被占用，当前窗口列表没有 agent-friend 设置窗，因此当时未截图。 |
| 2026-06-29 | 根据 review 调整设置页交互：从按凭据 group 平铺改为按能力分为对话模型、Web Search、语音通话与语音输入，并明确 saved secret 不回显、只能覆盖/删除 keyring 值 |
| 2026-06-29 | 交互调整验证通过：`pnpm --dir frontend typecheck`、`pnpm --dir frontend lint`、`pnpm --dir frontend lint:colors`、`pnpm --dir frontend lint:tokens`、`pnpm --dir frontend lint:ui-provenance`、`pnpm --dir frontend exec vitest run src/lib/model-config/index.test.ts` |
| 2026-06-29 | 修复 legacy `.env` 状态探测：dev 脚本向 Tauri 注入 `AGENT_FRIEND_REPO_ROOT`，Tauri Core 也会从 `frontend/src-tauri` 等嵌套目录向上识别 agent-friend repo root；验证通过 `cargo test --manifest-path frontend/src-tauri/Cargo.toml runtime_config` |
| 2026-06-30 | 根据实际交互反馈继续收敛：对话模型与记忆模型拆分，`DEEPSEEK_MEMORY_MODEL` 纳入 settings / runtime_config 读取链路并支持 legacy env 迁移；设置页抽出 `SettingsPageLayout`、`RuntimeSecretField`、`RuntimeSecretList`；保存后自动重启登记为 issue 031，不纳入本期实现。 |
| 2026-06-30 | 语音输入链路补齐 `VOLC_SPEECH` 凭证接入，并让前端在 provider 缺失、启动超时、停止卡住时回到可交互状态；新增 `frontend/src/stores/voiceInput.test.ts` 与 `voice_bridge/tests/unit/test_runtime_config.py`。 |
| 2026-06-30 | 最终验证通过：`./scripts/check/run.sh` 全绿（ruff、format、mypy、657 个 Python tests、frontend eslint / typecheck / vitest 34 files 187 tests）；`cargo test --manifest-path frontend/src-tauri/Cargo.toml settings` 通过；`cargo test --manifest-path frontend/src-tauri/Cargo.toml runtime_config` 通过；`git diff --check` 通过。 |
| 2026-06-30 | 视觉验收边界确认：本需求设置页 UI 是基础能力雏形，最终视觉不锁死；迭代中用户已基于实际设置页截图反馈布局问题并完成调整，因此本期不把正式桌面截图作为收尾硬门槛。`desktop-visual doctor` 通过，当前未启动 agent-friend 设置窗，未做最终截图。 |
| 2026-06-30 | 用户确认需求收尾；本文档总体状态切为 `COMPLETED`。 |
