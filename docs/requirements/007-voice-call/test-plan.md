# 007 · voice-call · 测试计划

> 本文档承载本需求的"测试用例与策略"，对应 [`docs/requirements/README.md`](../README.md) §文件命名约定中的 `test-plan.md`。
>
> 内容分两段：
>
> - **§1 自动化 AC**：在公司电脑 / 任何机器上**本地纯 mock**就能跑完，是合并 main 的硬门槛
> - **§2 Win 端到端 smoke**：在合规允许公网穿透的个人 Windows 电脑上跑，验证真火山 RTC + 浏览器全链路；这一步**不在 AC 列表内**，但合并 main 前必须由开发者人工确认通过（[`requirement.md`](./requirement.md) §6.10）
>
> 与 `requirement.md` / `design.md` 的关系：本文件**不**重复 AC 描述（那是 `requirement.md` §6 的事），只描述"怎么验证 AC 通过"和"smoke 怎么跑"。

---

## 1. 自动化 AC 测试

### 1.1 一行跑全量

```bash
# mac / linux
./scripts/test/run.sh

# windows
.\scripts\test\run.ps1
```

期望输出：

```
============================= 145 passed in ~2s ==============================
```

数字会随测试增减漂移；关键是**全绿**、不退化。

### 1.2 AC ↔ 测试文件映射

> 详细 AC 文本看 [`requirement.md`](./requirement.md) §6。本表只回答"AC-N 在哪条测试里被覆盖"。

| AC | 覆盖位置 |
|---|---|
| AC-1 控制平面拨打 | `voice_bridge/tests/integration/test_acceptance_criteria.py::TestAC1CallStart` |
| AC-2 控制平面挂断 | 同上 `::TestAC2CallStop` |
| AC-3 通话状态机 | 同上 `::TestAC3StateMachine` |
| AC-4 LLM 代理 session 注入 | 同上 `::TestAC4LLMProxySessionInject` |
| AC-5 channel 字段贯穿（新 session） | `::TestAC5ChannelOnNewSession` + `agent_bridge/tests/test_meta_channel_routes.py::TestCreateSessionEndpoint` + `agent/tests/test_channel.py::TestSessionNewWithChannel` |
| AC-6 channel 互切 | `::TestAC6ChannelSwitch` + `agent_bridge/tests/test_meta_channel_routes.py::TestSwitchChannelEndpoint` + `agent/tests/test_channel.py::TestConversationSwitchChannel` |
| AC-7 跨进程错误兜底 | `::TestAC7ErrorFallback` + `voice_bridge/tests/unit/test_errors.py` |
| AC-8 既有不退化 | `agent/tests/test_*.py` 全套 + `agent_bridge/tests/test_*.py` 全套继续通过；老 session（无 `initial_channel`）兼容性见 `agent/tests/test_channel.py::TestCurrentChannelDerivation::test_old_session_fallback_to_text` |
| AC-9 §3.19 / spike 不违反 | 改动控制：`data/` 下无新增子目录、`experiments/voice-poc/` 0 改动；通过 `git diff main..feature/007-voice-call` 人工核对 |
| AC-10 启动脚本 + smoke 客户端就位 | `scripts/voice/{run.sh,run.ps1,tunnel.sh}` + `scripts/README.md` 表格登记；`voice_bridge/smoke/index.html` + `voice_bridge/smoke/README.md` |

### 1.3 还可以单独跑某个 AC

```bash
# 只跑 voice_bridge 的 AC 集成测试
uv run pytest voice_bridge/tests/integration/

# 只跑 channel 相关单测
uv run pytest agent/tests/test_channel.py agent_bridge/tests/test_meta_channel_routes.py

# 只跑 AC-1
uv run pytest voice_bridge/tests/integration/test_acceptance_criteria.py::TestAC1CallStart
```

### 1.4 lint / typecheck / format 一并通过

合并 main 的额外硬要求：

```bash
./scripts/lint/run.sh         # ruff check + ruff format --check
./scripts/typecheck/run.sh    # mypy strict（含 voice_bridge）
```

任何一条不绿 → 不能合 main。

---

## 2. Win 上端到端 smoke（合并 main 前的人工确认项）

> ⚠️ **不要在公司电脑 / 公司网络做这一段**——会触发公网穿透合规问题（[`requirement.md`](./requirement.md) §6.10 / 开发对话已确认）。仅在个人 Windows + 家里网络做。
>
> 这一段不在 AC 列表内（AC 的"mock + 可重复"原则不允许真火山调用），但**合并 main 必须由开发者人工跑通这一段并明确确认 OK**。

### 2.1 一次性前置

只需做一次：

| 工具 | 安装命令（PowerShell） | 用途 |
|---|---|---|
| **uv** | `powershell -c "irm https://astral.sh/uv/install.ps1 \| iex"` | Python 包管理 / 虚拟环境 |
| **cloudflared** | 见 [Cloudflare 官方安装文档](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/) （Windows 安装包 `.msi`，装完 `cloudflared --version` 能跑即可） | 公网穿透。**无需登录**——本期只用 quick tunnel 模式 |

第一次跑 `.ps1` 脚本如果报 "running scripts is disabled"，按 [`scripts/README.md`](../../../scripts/README.md) §"Windows PowerShell 第一次运行"那段处理（仅当前用户范围、一次性）。

### 2.2 拉代码 + 装环境

```powershell
# 在你想放代码的目录下
git clone <agent-friend-repo-url> agent-friend
cd agent-friend
git checkout feature/007-voice-call

# 一键装好 venv + 依赖 + 生成 .env 模板
.\scripts\setup\run.ps1
```

`setup` 跑完会留下一个 `.env` 文件（拷自 `.env.example`），下一步就是填它。

### 2.3 编辑 `.env`

打开仓库根目录的 `.env`，确认 / 填入这几个 key：

| key | 怎么拿 | 备注 |
|---|---|---|
| `DEEPSEEK_API_KEY` | DeepSeek 控制台 | agent 的大脑用 |
| `VOLC_ACCESS_KEY` / `VOLC_SECRET_KEY` | 火山引擎 → API 访问密钥 | 调火山 OpenAPI 用 |
| `VOLC_RTC_APP_ID` / `VOLC_RTC_APP_KEY` | 火山引擎 → RTC 控制台 → 应用 | RTC 房间签名用 |
| `VOLC_SPEECH_APP_ID` / `VOLC_SPEECH_ACCESS_TOKEN` | 火山引擎 → 语音技术 → 应用 | ASR / TTS 凭证 |
| `VOICE_BRIDGE_PUBLIC_URL` | **§2.4 起完 cloudflared 后回填** | 火山 RTC 回调 voice_bridge 的入口 URL |

> 上面的 `VOLC_*` 凭证 spike 阶段已经申请并填过；如果你有 spike 时期的 `.env` 备份，直接复用即可。spike 与 007 共用同一套火山凭证（[`requirement.md`](./requirement.md) AC-9 / 设计选择）。

### 2.4 三个终端起服务

**Terminal A —— agent_bridge**：

```powershell
.\scripts\bridge\run.ps1
```

跑稳后日志会看到 `Uvicorn running on http://127.0.0.1:18800`。

**Terminal B —— cloudflared 公网穿透**（先于 voice_bridge 起，因为它的 URL 要回填进 `.env`）：

cloudflared 在 Windows 上没有专门的脚本（`scripts/voice/tunnel.sh` 是 mac/linux 单端例外）。直接跑：

```powershell
cloudflared tunnel --url http://127.0.0.1:18900
```

输出里会有一行类似：

```
Your quick tunnel has been created! Visit it at:
  https://random-words-here.trycloudflare.com
```

复制这个 URL（含 `https://`、不含末尾斜杠），打开 `.env` 把 `VOICE_BRIDGE_PUBLIC_URL` 改成这个值。

**Terminal C —— voice_bridge**（必须在 `VOICE_BRIDGE_PUBLIC_URL` 填好之后再起）：

```powershell
.\scripts\voice\run.ps1
```

跑稳后日志会看到 `Uvicorn running on http://127.0.0.1:18900`。

> ⚠️ **最容易踩坑的一步**：如果 cloudflared URL 改了（比如重启拿到新随机域名），必须**重启 Terminal C 的 voice_bridge**——配置只在启动时读一次。

### 2.5 浏览器 smoke

双击打开 `voice_bridge/smoke/index.html`（或在 Terminal D 跑 `python -m http.server 8000` 然后访问 `http://127.0.0.1:8000/voice_bridge/smoke/`，避免 `file://` 协议在某些浏览器下被 RTC SDK 拒绝）。

操作：

1. 顶部输入框保持 `http://127.0.0.1:18900`（即 voice_bridge 本机地址，浏览器和 voice_bridge 在同一台机器，不需要走 cloudflared）
2. "可选：续上已有 session_id" 留空（首次 smoke 走新建 session 路径）
3. 点 **"拨打通话"** → 浏览器弹麦克风权限授权
4. 等约 1 秒，AI 进房后会主动说欢迎语
5. 直接说话，AI 用语音回复
6. 说几句后点 **"挂断"** 结束

### 2.6 怎么算 smoke 通过

四条都满足才算通过：

- ✅ AI 在 1~3 秒内进房说出欢迎语（默认 "嗨呀，我是你的 AI 朋友，咱们随便聊聊"）
- ✅ 用户语音说一句（如"你好，今天天气怎么样"），AI 用**语音**做出**符合 persona、贴近真人**的回复（短句、口语化，不像念稿）；不是机械客服腔
- ✅ 挂断后浏览器页面 state pill 变成 `stopped`，Terminal C 的 voice_bridge 日志里看到 `state=stopped` 类的日志
- ✅ 重新拨打第二通通话不报错，凭证、call_id 是新的

### 2.7 失败排查指引

| 现象 | 先看哪 |
|---|---|
| 拨打按钮点了没反应、浏览器 console 报错 | 浏览器 DevTools 的 Network 标签页 + Console；voice_bridge 终端有没有日志；火山凭证（特别是 `VOLC_ACCESS_KEY` / `SECRET_KEY`）是否粘对 |
| AI 没进房、超过 5 秒还是静音 | Terminal B（cloudflared）有没有看到 POST 请求经过；Terminal C voice_bridge 日志有没有 `agent_bridge` 调用错误；`VOICE_BRIDGE_PUBLIC_URL` 是不是当前 cloudflared 域名 |
| AI 进房但 LLM 不回复（用户说话后没反应） | Terminal A agent_bridge 日志有没有 SSE 流出去；`DEEPSEEK_API_KEY` 是否有效；voice_bridge 日志有没有 `X-Agent-Friend-Session-Id` 注入日志 |
| 挂断后 voice_bridge 报 stopped 但下次拨打 503 | 火山限流（`volc_rate_limited`）；等 30 秒重试 |
| voice_bridge 启动报 `VOICE_BRIDGE_PUBLIC_URL` 缺失 | `.env` 没填或没生效——确认 `.env` 在仓库根、且 `voice_bridge` 起的时候是从仓库根目录起的 |

更细的错误码 → 用户提示语映射，看 [`voice_bridge/src/voice_bridge/errors.py`](../../../voice_bridge/src/voice_bridge/errors.py)。

### 2.8 smoke **不**验证的事（避免误判范围）

- 文字 ↔ 语音 channel 互切的真实体感差异（AC-6 已经在 mock 层覆盖；端到端体感差异属于"调 prompt"的后续优化范畴，不是本期范围）
- 多并发通话稳定性（本期单用户 smoke 即可）
- 长时间通话（超过 5 分钟）的 RTC 链路稳定性（火山侧自带 idle 超时回收，spike 已观察过；本期不重复验证）
- 公网外用户接入（smoke 只验证本机浏览器 → 本机 voice_bridge → cloudflared 回环 → 火山）

---

## 3. 合并 main 前的最终清单

走完 [`dev-workflow.mdc`](../../../.cursor/rules/dev-workflow.mdc) §"合入 main 的前置条件"前，自检：

- [ ] §1.1 全量 pytest 145 全绿
- [ ] §1.4 lint + typecheck 全绿
- [ ] §2 Win smoke 四条都达成
- [ ] `git diff main..feature/007-voice-call` 没有违反 AC-9（`data/` 不动、`experiments/` 不动）
- [ ] 用户（你自己）明确说"验收通过、可以合"

四条全满足才合 main。
