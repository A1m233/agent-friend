# QQ 语音消息输入 - 进度

## 总体状态

<!-- NOT_STARTED | IN_PROGRESS | COMPLETED | BLOCKED -->
IN_PROGRESS

## 任务列表

- [x] 补齐 voice_bridge 一次性音频转写 helper 与 HTTP 入口
- [x] 补齐 agent_bridge QQ 语音入站 resolver、voice_bridge client 与消息去重
- [x] 将 resolver 装配进 IMRuntime，保持普通文本消息原行为
- [x] 补充 voice_bridge 与 agent_bridge 的 fake/mock 自动化测试
- [x] 跑项目门禁并记录结果
- [x] 完成真实 QQ 语音 smoke 或记录未执行原因

## 实现日志

| 日期 | 完成内容 | 备注 |
|------|---------|------|
| 2026-06-29 | Phase 3 开始，创建实现进度文档 | 用户已明确“开始实现” |
| 2026-06-29 | 完成 voice_bridge one-shot 转写 helper 与 `/voice/transcriptions/audio` 入口 | 局部测试 `uv run pytest voice_bridge/tests/unit/test_transcription_route.py` 通过 |
| 2026-06-29 | 完成 agent_bridge QQ 语音 resolver、voice_bridge client、入站去重与 IMRuntime 装配 | 局部测试 `uv run pytest agent_bridge/tests/test_im_voice.py agent_bridge/tests/test_im_voice_bridge_client.py agent_bridge/tests/test_im_runtime_inbound.py` 通过 |
| 2026-06-29 | 完成新增 fake/mock 测试，并回归相关旧测试 | `uv run pytest voice_bridge/tests/unit/test_transcription_route.py agent_bridge/tests/test_im_router.py agent_bridge/tests/test_im_qq_adapter.py agent_bridge/tests/test_im_runtime_onboard.py agent_bridge/tests/test_assembly.py agent_bridge/tests/test_settings.py` 通过 |
| 2026-06-29 | 项目统一门禁通过 | `./scripts/check/run.sh` 通过；646 个后端/voice 测试通过；前端部分因本机依赖未就绪由脚本跳过 |
| 2026-06-29 | 真实 QQ 语音 smoke 已获授权并尝试，但未收到 QQ 入站事件 | 已用当前 feature worktree 启动 bridge/voice，确认 `/voice/transcriptions/audio` 已加载、QQ provider active；等待约 7 分钟仅见 health/prewarm 日志，无 QQ message event，因此未形成端到端回复验证。期间发现 voice_bridge ASR prewarm 返回 unavailable；尝试后已停止 feature worktree 服务，并恢复原 checkout 的 18800/18900 服务 |
| 2026-06-29 | 定位 ASR fallback 凭据复用方式 | 当前 `.env` 缺 `VOLC_SPEECH_ACCESS_TOKEN`，且 `VOLC_SPEECH_APP_ID` 与已验证 spike 配置不一致；临时启动 18901 voice_bridge 并注入 spike `Custom.json` 中的语音 AppId+AccessToken 后，`/voice/transcriptions/prewarm` 返回 `status=started`，证明无需新 key，需复用旧 spike ASR token |
| 2026-06-29 | 用户确认接受真实 QQ 语音 smoke 暂缓并要求合入主干 | 保留真实 QQ 入站端到端验证为后续补跑项；本次合入以 fake/mock 自动化、统一门禁与 ASR fallback 凭据复用验证为准 |
