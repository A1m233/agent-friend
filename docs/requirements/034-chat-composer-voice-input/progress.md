# Chat Composer 语音输入 - 进度

## 总体状态

COMPLETED

## 任务列表

- [x] M34.0：核对 ASR 上游协议 / 音频格式，确定实时路径或回退策略
- [x] M34.1：实现 voice_bridge ASR provider interface、fake/disabled provider 与 WebSocket route
- [x] M34.2：补齐 voice_bridge 转写日志字段与错误返回
- [x] M34.3：实现前端 recorder、voiceInput WebSocket client 与状态机
- [x] M34.4：接入 Chat Composer 语音按钮、draft adapter 与 ASR 文本追加
- [x] M34.5：处理错误、取消、发送中停止和 trace 关联
- [x] M34.6：补自动化测试并跑必要门禁
- [x] M34.7：接入已验证的真实 ASR provider（Volc direct streaming）
- [x] M34.8：用真实凭证和麦克风完成本机 smoke 验收

## 实现日志

| 日期 | 完成内容 | 备注 |
|------|---------|------|
| 2026-06-27 | 开始 Phase 3 实现 | 用户已明确授权“开始实现” |
| 2026-06-27 | 完成 voice_bridge 转写 WebSocket 协议层、ASR provider seam、日志和单测 | 生产默认 provider 明确返回 `asr_provider_unavailable` |
| 2026-06-27 | 完成前端 recorder、voiceInput store、Composer 语音按钮和 draft 追加 | 使用 `pcm16/16k/mono` 作为前端输出格式，保留后续切换 provider/one-shot 的口子 |
| 2026-06-27 | 跑通全量 `scripts/check/run.ps1` | ruff / mypy / pytest 605 / frontend lint + typecheck + vitest 141 全部通过 |
| 2026-06-27 | 完成火山官方 ASR 协议调研 | 官方文档确认支持 WebSocket 流式 ASR；复用现有 `VOLC_SPEECH_APP_ID` / `VOLC_SPEECH_ACCESS_TOKEN`；非敏感 `VOLC_SPEECH_RESOURCE_ID` 默认 `volc.bigasr.sauc.duration` |
| 2026-06-27 | 接入真实 Volc streaming ASR adapter | 默认使用双向流式优化版 `wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async`；前端 PCM16/16k/mono 映射到火山 `pcm/raw/16000/16/1`；支持新版 `VOLC_SPEECH_API_KEY` 覆盖旧鉴权 |
| 2026-06-27 | 补充 Volc adapter 单测 | 覆盖鉴权 header、resource id 默认值、二进制帧 gzip/json 编解码、partial/final 解析、最后一包标记 |
| 2026-06-27 | 调整停止后 ASR drain 窗口并重跑全量 `scripts/check/run.ps1` | ruff / mypy / pytest 605 / frontend lint + typecheck + vitest 141 全部通过 |
| 2026-06-27 | 补齐语音输入冷启动排查日志 | 前端新增 `[voice-input][latency]` 阶段耗时；后端新增 `voice_input_ready`，用于拆分 ASR provider 初始化与首帧音频耗时 |
| 2026-06-27 | 实现 ASR connect-only prewarm 与前端 ready-to-speak 交互 | 预热只建联不发送 full request / audio；Composer 可用时循环续热，点击语音后本地 recorder ready 即进入可说话状态；全量 `scripts/check/run.ps1` 通过 |
| 2026-06-28 | 补齐 prewarm 旧进程防呆 | smoke 日志发现前端 prewarm 404 且后端无 `voice_input_prewarm_*`，定位为 dev/run 复用旧 `voice_bridge`；新增 `/voice/transcriptions/healthz` 并在 dev 脚本复用前校验，旧进程会自动重启 |
| 2026-06-28 | 完成重启后真实 smoke 与日志验收 | 前端 `voice-input-f71b93a0-4112-48e8-a516-8991de526aa9` 在 450ms ready-to-speak、464ms server-ready；后端消费 warm connection，`voice_input_ready` 5ms，随后 first_audio / partial / final / stop 闭环；prewarm 前后端 trace 成对出现 |
