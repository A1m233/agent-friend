# 036 · 桌面端运行时配置管理 - 实现进度

## 状态

COMPLETE

## 任务

- [x] 补齐 Python runtime credential registry，加入当前语音输入会用到的 `VOLC_SPEECH_API_KEY`
- [x] 扩展桌面 settings，加入 DeepSeek 主模型设置
- [x] 新增 Tauri runtime config facade，支持 secret 状态查询、写入、删除
- [x] 新增前端 runtime config typed client / hook
- [x] 抽出 DeepSeek 默认模型解析，并把新会话 requested model 带到 AG-UI 请求
- [x] 扩展 bridge decoder / route，仅允许两个 DeepSeek 模型用于新 session auto-create
- [x] 设置中心接入基础查看 / 修改 UI
- [x] 补充自动化测试并运行验证
- [x] 做桌面端视觉验收或记录无法验收原因

## 日志

| 时间 | 记录 |
|------|------|
| 2026-06-29 | Phase 3 开始：按 design.md 实现查看 / 修改能力 |
| 2026-06-29 | 完成 runtime_config registry 补齐、Tauri keyring facade、前端 hook、DeepSeek 模型设置链路和设置页雏形 |
| 2026-06-29 | 验证通过：`cargo check --manifest-path frontend/src-tauri/Cargo.toml`、`cargo test --manifest-path frontend/src-tauri/Cargo.toml runtime_config`、`uv run pytest runtime_config/tests/test_sources.py agent_bridge/tests/test_ag_ui_decoders.py`、`pnpm --dir frontend typecheck`、`pnpm --dir frontend lint`、`pnpm --dir frontend lint:colors`、`pnpm --dir frontend lint:tokens`、`pnpm --dir frontend lint:ui-provenance`、`pnpm --dir frontend test` |
| 2026-06-29 | 桌面视觉验收：`desktop-visual doctor` 通过；真实启动被审批拦截，因为脚本可能自动清理 1420 端口旧进程。已确认 1420 被占用，当前窗口列表没有 agent-friend 设置窗，因此未截图。需要用户明确允许清理 1420 后再跑真实窗口截图。 |
| 2026-06-29 | 根据 review 调整设置页交互：从按凭据 group 平铺改为按能力分为对话模型、Web Search、语音通话与语音输入，并明确 saved secret 不回显、只能覆盖/删除 keyring 值 |
| 2026-06-29 | 交互调整验证通过：`pnpm --dir frontend typecheck`、`pnpm --dir frontend lint`、`pnpm --dir frontend lint:colors`、`pnpm --dir frontend lint:tokens`、`pnpm --dir frontend lint:ui-provenance`、`pnpm --dir frontend exec vitest run src/lib/model-config/index.test.ts` |
| 2026-06-29 | 修复 legacy `.env` 状态探测：dev 脚本向 Tauri 注入 `AGENT_FRIEND_REPO_ROOT`，Tauri Core 也会从 `frontend/src-tauri` 等嵌套目录向上识别 agent-friend repo root；验证通过 `cargo test --manifest-path frontend/src-tauri/Cargo.toml runtime_config` |
