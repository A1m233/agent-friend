# voice_bridge / smoke

> ⚠️ **非产品代码**。本目录提供一个最小 HTML+JS 客户端，**仅供开发者在合规允许公网穿透的环境跑端到端 smoke 验证**。
>
> Windows 上一键启动：`.\scripts\voice-smoke\run.ps1`（会拉起 agent_bridge、cloudflared、voice_bridge 和本地 HTTP server）。
>
> 不是产品级前端、不在 AC 列表内、不做错误处理 UI 美化、未来桌宠 / IM / Web 客户端的 voice 接入有各自的产品级实现。

## 完整跑通流程在哪里

**端到端怎么跑（含装环境、配 `.env`、起服务、浏览器操作、失败排查）**——统一看：

➡️ [`docs/requirements/007-voice-call/test-plan.md`](../../docs/requirements/007-voice-call/test-plan.md) §2

本文件只承载"这个目录是什么、不能用来做什么"，避免双份说明跟着代码漂。

## 用途

验证 007 voice-call 整链路：

surface（浏览器）→ voice_bridge → 火山 RTC → AI 进 RTC 房间 + ASR/TTS → voice_bridge LLM proxy → agent_bridge → agent → DeepSeek → 通过 RTC 回到浏览器（语音）

## 不要做的事

- **不要**把 voice_bridge bind 到 `0.0.0.0`——公网穿透 URL 是唯一对外入口
- **不要**用本目录的 HTML 作为产品交付件
- **不要**把火山凭证写进 HTML / 提交到版本控制（`.env` 已在 `.gitignore`）
