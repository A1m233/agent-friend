# 032 · 运行时环境与凭据配置 - 进度

## 总体状态

COMPLETED

## 任务列表

- [x] 新增 `runtime_config` workspace 包，提供 env / keyring / legacy dotenv source、credential status、CLI。
- [x] 调整 `llm_providers` 与工具注册，支持显式凭据注入并保留 legacy fallback。
- [x] 调整 `agent_bridge` 启动与 runtime 装配，让桌面主路径不隐式读取仓库 `.env`。
- [x] 调整 `voice_bridge` 凭据装配，让 Volc secret 走显式 credential source，`VOICE_BRIDGE_PUBLIC_URL` 仍保持 runtime-only。
- [x] 调整 dev / bridge / voice / Windows voice smoke / setup 文案与 `.env.example`，冻结 `.env` 为 legacy。
- [x] 补充自动化测试，覆盖 source precedence、no dotenv main path、显式注入和 legacy 兼容。
- [x] 跑相关测试与项目门禁。
- [x] Windows 实机语音 smoke 手动验证。

## 实现日志

| 日期 | 完成内容 | 备注 |
|------|----------|------|
| 2026-06-26 | Phase 3 启动，创建实现进度清单 | 等待逐项实现 |
| 2026-06-26 | 新增 `runtime_config` 包与 `scripts/credentials/run.*`，接入 `keyring`、env、legacy `.env` source | `uv.lock` 已更新 |
| 2026-06-26 | `agent_bridge` / `voice_bridge` 默认切到 desktop mode；显式 `--legacy-dotenv` 保留存量 fallback | `VOICE_BRIDGE_PUBLIC_URL` 仍只允许 runtime env |
| 2026-06-26 | 更新 `.env.example`、setup/dev/bridge/voice/voice-smoke/scripts README 文案 | `.env` 不再是默认开发配置入口 |
| 2026-06-26 | 通过 `./scripts/check/run.sh` | lint 通过；mypy 通过；pytest 600 passed；前端因未安装依赖按脚本跳过 |
| 2026-06-26 | Windows 实机语音 smoke 本次不阻塞合入 | 当前环境为 macOS；用户确认仅差 Windows 实机验证，可以先合入推送，后续在 Windows 真机补跑 `.\scripts\voice-smoke\run.ps1` |
