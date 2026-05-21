# voice-poc · 语音方案 C 可行性 spike

> **这是 spike，不是正式实现。** 唯一目标：用最低代价拿到"火山 RTC AI 对话 + 自定义 LLM"方案能否承载 `agent-friend` 语音对话需求的判断。
>
> **状态：已提前结束（2026-05-21）。** Phase 0（链路连通性）已验通过，证明 C 方案技术上跑得通；Phase 1/2 不在 spike 内继续，已合入 main 留档。语音对话的"自家 LLM 接管 + agent-bridge 公网暴露"作为正式需求另立——预计编号 `006-voice-io`（`005` 已被 `feature/005-engine-tool-calling-and-web-search` 占用）。详见 [`SPIKE-NOTES.md`](./SPIKE-NOTES.md) 末尾的"最终结论"。

## 背景

调研结论详见上游对话与 [`docs/decisions/0001-product-vision-and-roadmap`](../../docs/decisions/0001-product-vision-and-roadmap/README.md) 路线图。简而言之：

- **A 方案** = 豆包端到端实时语音大模型（`RealtimeAPI`），体验最好但大脑是豆包
- **B 方案** = 火山流式 ASR + 流式 TTS 自拼，自家大脑但失去全双工/打断
- **C 方案** = 火山 RTC AI 对话 + 自定义 LLM（`CustomLLM`），既要体验又要自家大脑 —— **本 spike 的验证对象**

C 方案有一个已经确认的物理约束：火山 RTC 调度器跑在火山云端，所以**自家 LLM endpoint 必须公网可达**。开发期靠内网穿透解决，产品化阶段需要再决策。

## 目录结构

```text
experiments/voice-poc/
├── README.md                # 本文件
├── SPIKE-NOTES.md           # spike 最终交付物（验收记录 + 决策建议）
├── scripts/
│   ├── run-server.sh        # 启动 RTC AIGC Server (port 3001)
│   └── run-web.sh           # 启动前端 (port 3000)
├── rtc-aigc-demo/           # Phase 0/1：clone 官方 demo，已加入 .gitignore
└── agent-bridge/            # Phase 2：最小 FastAPI 包装层（独立 uv 项目）
```

## Phase 0 启动方法

需要**两个 terminal 同时运行**，且当前 node 必须是 v16+（已验证 v20.19.3 OK）、yarn 必须是 v1 classic（已通过 corepack 装好）。

**Terminal A —— Server**：

```bash
./experiments/voice-poc/scripts/run-server.sh
# 看到 "AIGC Server is running at http://localhost:3001" 即成功
```

**Terminal B —— 前端**：

```bash
./experiments/voice-poc/scripts/run-web.sh
# 看到 "Compiled successfully!" 即成功，浏览器会自动打开
```

浏览器进入后：

1. 点页面上的麦克风按钮，允许浏览器麦克风权限
2. 开始说话，验证完整链路：你说话 → AI 听到并回复

调用关系：

```text
浏览器 ←(WebRTC)→ 火山 RTC 云端 ←(OpenAPI signed by AK/SK)→ Server (localhost:3001) ←(读取)→ scenes/Custom.json
                            ↓
                       火山自家方舟 LLM (ARK endpoint)
                            ↓
                       火山自家 ASR + TTS
```

**注意**：第一次启动 = 真实 LLM API 调用，会扣方舟 token。spike 期间体验完后及时关掉，避免空跑。

## 三阶段流程

详见 `SPIKE-NOTES.md` 与原始 plan 文件。

```text
Phase 0 → 跑通官方 demo（火山自家 LLM）  → 验证 RTC 链路体验上限
Phase 1 → 切到 DeepSeek（CustomLLM）     → 验证 OpenAI 协议兼容性
Phase 2 → 切到自家 agent-bridge          → 验证记忆 + persona 注入
```

每阶段跑完都有"是否进入下一阶段"的验收点，避免一路冲到底才发现根本问题。

## 工程规范豁免

按 [`.cursor/rules/coding-design.mdc`](../../.cursor/rules/coding-design.mdc) 和 [`.cursor/rules/cross-platform-dev.mdc`](../../.cursor/rules/cross-platform-dev.mdc) 的精神，本目录下的代码因为是 **spike，不进入 main**，允许以下豁免：

- 启动脚本只提供 mac（`run.sh`），不需要同步 win `run.ps1`
- 不强制 ruff/mypy/lint 全绿
- 不进 `scripts/README.md` 表格登记
- 测试不强制
- 临时硬编码参数允许（敏感 key 仍走 `.env`）

但下列约束**仍然适用**：

- API key 不入库（走 `.env` / `.env.example`）
- 真实 LLM API 调用前必须征得用户同意（[llm-api-confirm](../../.cursor/rules/llm-api-confirm.mdc)）
- 公网穿透 URL 不入库（仅在本地 demo 配置文件中）

## 不可撤回操作清单

按个人 rule [global-user-rules](../../.cursor/rules/) 的精神，下列动作执行前必须先告知用户：

1. **火山引擎账号实名认证**（用户自己在控制台做，agent 不介入）
2. **真实 LLM API 调用**（Phase 0 消耗方舟 token，Phase 1+2 消耗 DeepSeek token）
3. **启动 cloudflared 公网隧道**（Phase 2 暴露本地 FastAPI 到公网）
