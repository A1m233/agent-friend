# 022 · IM 通道接入 + 首条 QQ Adapter — 进度

## 总体状态

<!-- NOT_STARTED | IN_PROGRESS | COMPLETED | BLOCKED -->
IN_PROGRESS

## 需求文档 / 技术方案

- → [requirement.md](./requirement.md)
- → [design.md](./design.md)

## 任务粒度说明

实施载体:**当前 worktree `prancy-waddling-pebble`** 对应分支 `feature/022-im-channel-and-qq-adapter`(从最新 origin/main 切)。每个里程碑独立可验证(`./scripts/check` 全绿 = 里程碑通过门槛);手动真跑 AC 验收由用户在 M22.10 在 macOS 跑一遍。

实施顺序按 design.md §2 的依赖关系组织:**先底层数据类型 + 凭据 → 路由逻辑 → 平台 adapter → 生命周期 + onboard → HTTP routes → bridge 装配 → 前端 dialog 与 actionbar → smoke → 自动化门禁 → 真跑收尾**。每个里程碑里"涉及文件 + 涉及单测"明确列出,便于断点续传。

---

## 里程碑与任务

### M22.1 依赖装配 + 模块脚手架

- [x] `agent_bridge/pyproject.toml` 加 `qqbot-agent-sdk[qrcode]>=1.2.2` 依赖;`cryptography>=42` 如果不是传递依赖则显式加
- [x] `uv sync` 跑通(根目录 workspace 同步)
- [x] 新建 `agent_bridge/src/agent_bridge/protocols/im/` 目录 + 空 `__init__.py`(暂时仅留 module docstring,实际 export 等后续里程碑填)
- [x] 新建 `agent_bridge/src/agent_bridge/protocols/im/adapters/` 子目录 + 空 `__init__.py`

**门槛**:`uv sync` 成功;`./scripts/check` 不退化(不含新增内容,只验依赖 baseline)。

### M22.2 后端核心数据类型 + 凭据加密 + 单测

> 落地后`event.py` / `content.py` / `provider.py` / `credentials.py` 四个文件可独立 import 跑测,不依赖其他后续模块。

- [x] `protocols/im/event.py`:`from qqbot_agent_sdk.event_parser import InboundEvent`(直接借用 SDK 的 platform-agnostic shape);加 docstring 说明"未来若第二条 adapter 不基于 qqbot-agent-sdk,这里改成 protocol-internal dataclass"
- [x] `protocols/im/content.py`:`OutboundContent` frozen dataclass(`chat_id` / `chat_scope` / `text` / `reply_to_message_id`)
- [x] `protocols/im/provider.py`:`IMProvider` Protocol(`type` / `bind_id` / `start` / `send` / `stop` / `status`)+ `ProviderStatus = Literal["active", "degraded", "error", "stopped"]`
- [x] `protocols/im/credentials.py`:`ImCredential` dataclass + `CredentialStore`(`_derive_key` / `save` / `list_all` / `delete` / `_path_for` / `_load`);base_dir 由调用方注入(测试时注 tmp 目录,生产用 `agent.user_data_dir() / "im_credentials"`)
- [x] `agent_bridge/tests/test_im_credentials.py`:
  - [x] `save → list_all` round-trip:保存 1 个 cred,list 出来字段完全一致
  - [x] `save` 2 个 → `list_all` 2 个,删 1 个 → `list_all` 1 个
  - [x] `_derive_key` 同输入 same machine 同输出
  - [x] wrong key 解密失败时 `list_all` 跳过(不抛)
  - [x] 文件名 hash 路径稳定(`qq_<bind_id_hash[:16]>.json.enc`)
- [x] `./scripts/check` 全绿

**门槛**:四个模块文件创建 + credentials 单测全过 + lint/typecheck 不退化。

### M22.3 IMRouter + 单测

> 完成后 `IMRouter` 可独立 import 跑测,通过 mock `SessionBridge` 验证 inbound→bind_persistent→TextDelta 聚合→outbound 全链路。

- [x] `protocols/im/router.py`:
  - [x] `FALLBACK_TEXT` 模块常量(`"我现在有点问题,稍后再试 🤖"` 或类似;不带 emoji 也行,跟用户拍最终文案)
  - [x] `IMRouter.__init__(runtime: BridgeRuntime, session_bridge: SessionBridge)`
  - [x] `IMRouter.session_id_for(im_type, event) -> str`:本期实装 `f"im:{im_type}:{event.chat_id}"`,docstring 标注扩展点
  - [x] `IMRouter.handle_inbound(im_type, event, send_fn) -> None`(async):装 `PersistentBootstrap` + `asyncio.to_thread(_run_turn_sync, boot)` + 异常 catch → `FALLBACK_TEXT` + 调 `send_fn(OutboundContent(...))`
  - [x] `IMRouter._run_turn_sync(boot) -> str`:`session_bridge.bind_persistent(boot)` → 同步 iterate `conv.stream` → 聚合 `TextDelta.text` → `TurnDone` 中断 → return 聚合文本
- [x] `agent_bridge/tests/test_im_router.py`:
  - [x] `session_id_for` 稳定输出 + 同 `chat_id` 同结果
  - [x] `handle_inbound` 调用 `session_bridge.bind_persistent` 时 `thread_id == session_id_for(...)`
  - [x] mock `Conversation.stream` 返回 `[TextDelta("hello"), TextDelta(" world"), TurnDone()]` → `send_fn` 收到 `OutboundContent.text == "hello world"`
  - [x] mock `Conversation.stream` 抛错 → `send_fn` 收到 `OutboundContent.text == FALLBACK_TEXT`
  - [x] `ToolCallRequest` / `ToolCallResult` 不污染 outbound(只 TextDelta 进 buffer)
  - [x] `send_fn` 收到的 `chat_id` / `chat_scope` / `reply_to_message_id` 跟 inbound event 一致
- [x] `./scripts/check` 全绿

**门槛**:Router 单测全过 + lint/typecheck 不退化。

### M22.4 QQ Adapter + 单测

> 完成后 `QQAdapter` 可独立 import 跑测,通过 mock `QQWebSocket` / `QQApiClient` 验证 callbacks 接线 + 错误码兜底 + resume token 文件 round-trip。

- [x] `protocols/im/adapters/qq.py`:
  - [x] `QQAdapter.__init__(cred: ImCredential, resume_base_dir: Path)`(后者注入便于测试)
  - [x] `QQAdapter.type = "qq"` class attribute
  - [x] `QQAdapter.start(on_inbound)`:构造 `QQApiClient` + `get_gateway_url_sync()` + 构造 `WSCallbacks` + `QQWebSocket(callbacks, log_tag).start(gateway_url, main_loop)`;`main_loop = asyncio.get_event_loop()`
  - [x] `QQAdapter._on_message(event_type, raw)`(async):`EventParser.parse` → 仅 c2c 转发到 `self._on_inbound(event)`
  - [x] `QQAdapter.send(content)`(async):构造 `MessageToCreate` → `await api.post_c2c_message` → check `resp["code"]` 非零仅 log warning
  - [x] `QQAdapter.stop()`(async):`ws.async_stop()` + `api.close()` + `_set_status("stopped")`
  - [x] `QQAdapter.status()`:返回当前 `_status`
  - [x] `_load_resume_token` / `_save_resume_token`:简单 json 文件 round-trip,路径 `resume_base_dir / f"qq_{bind_id_hash}.json"`
- [x] `agent_bridge/tests/test_im_qq_adapter.py`:
  - [x] mock `QQWebSocket` + `QQApiClient`,验证 `start` 调用顺序:先 `get_gateway_url_sync` → 后 `ws.start(gateway_url, main_loop)`
  - [x] `WSCallbacks.on_connected` → `status` 变 `active`;`on_disconnected` → `degraded`;`on_fatal_error` → `error`
  - [x] `_on_message` 调用 `EventParser.parse` → 仅 `chat_scope=="c2c"` 转发(group/guild 不转发)
  - [x] `send` 错误码非零(43xxx / 11xxx)仅 log warning 不抛
  - [x] `send` 网络异常 catch + log exception 不抛
  - [x] `_load_resume_token` 空文件 → `(None, None)`;有效文件 → `(session_id, last_seq)` tuple
  - [x] `_save_resume_token` 写出后 `_load_resume_token` 能读回
- [x] `./scripts/check` 全绿

**门槛**:QQ adapter 单测全过 + lint/typecheck 不退化。**不真接 QQ gateway**——所有 SDK 调用 mock 掉。

### M22.5 Onboard 注册表 + IMRuntime + 单测

> 完成后 `OnboardSessionRegistry` + `IMRuntime` 可独立 import 跑测,验证 onboard 状态机 + provider 生命周期。

- [ ] `protocols/im/onboard.py`:
  - [ ] `OnboardStatus` str Enum(`PENDING` / `QR_READY` / `SUCCESS` / `FAILED`)
  - [ ] `OnboardTaskState` dataclass(`task_id` / `im_type` / `status` / `qr_url` / `bind_id` / `error`)
  - [ ] `OnboardSessionRegistry.__init__(im_runtime: IMRuntime)`
  - [ ] `OnboardSessionRegistry.start(im_type) -> str`:仅支持 `qq`(其他 raise ValueError);异步 task `_run_qq_onboard`
  - [ ] `_run_qq_onboard(state)`(async):调 `qqbot_agent_sdk.start_onboard(on_qr_ready=...)` → 成功时构造 `ImCredential` → 调 `im_runtime.register_after_onboard(cred)` → status `SUCCESS`;异常 → `FAILED`
  - [ ] `OnboardSessionRegistry.get(task_id) -> OnboardTaskState | None`
- [ ] `protocols/im/runtime.py`:
  - [ ] `ProviderStatus` dataclass(`type` / `bind_id_masked` / `status`)
  - [ ] `IMRuntime.__init__(runtime, router, credentials)`
  - [ ] `IMRuntime.start()`:`credentials.list_all()` → 逐个 `_spawn_provider`
  - [ ] `IMRuntime.stop(timeout)`(async):并发 stop 所有 providers,timeout 守护
  - [ ] `IMRuntime.register_after_onboard(cred)`:`credentials.save(cred)` + `_spawn_provider(cred)`
  - [ ] `IMRuntime.unbind(im_type, bind_id)`(async):stop 对应 provider + `credentials.delete`
  - [ ] `IMRuntime.list_status() -> list[ProviderStatus]`
  - [ ] `IMRuntime._spawn_provider(cred)` + `_build_provider(cred)` + `_make_inbound_cb(provider)`
  - [ ] `IMRuntime._mask(bind_id) -> str`(辅助函数,头 4 字符 + ... + 尾 4 字符)
- [ ] `agent_bridge/tests/test_im_onboard.py`:
  - [ ] `OnboardSessionRegistry.start("qq")` 返回 `task_id`,初始 state 是 `PENDING`
  - [ ] mock `start_onboard`:`on_qr_ready` 被调时 state 变 `QR_READY` + `qr_url` 填上
  - [ ] mock `start_onboard` 成功返回 → state `SUCCESS`,`im_runtime.register_after_onboard` 被调一次,`bind_id` 是 masked 形式
  - [ ] mock `start_onboard` 抛错 → state `FAILED`,`error` 填上
  - [ ] `start("feishu")` raise ValueError
- [ ] `agent_bridge/tests/test_im_runtime.py`(可选简单测,如果觉得 onboard 测试覆盖了大部分 runtime 行为可以省;但 `unbind` + `stop` 行为单独覆盖更稳)
  - [ ] mock provider,验证 `start` 调用顺序、`stop` 并发、`unbind` 删除文件
- [ ] `./scripts/check` 全绿

**门槛**:onboard + runtime 单测全过。

### M22.6 HTTP routes + register_routes

> 完成后 `/v1/im/*` 四个端点可通过 FastAPI TestClient 调通(还没接到 app.py)。

- [ ] `protocols/im/routes.py`:
  - [ ] pydantic `OnboardStartBody`(`im_type: Literal["qq"]`)
  - [ ] `register_routes(app: FastAPI, runtime: BridgeRuntime)`:`APIRouter(prefix="/v1/im", tags=["im"])`
  - [ ] `GET /providers` → `runtime.im_runtime.list_status()` 序列化;`im_runtime is None` 时 `503`
  - [ ] `POST /onboard/start` → `runtime.im_runtime._onboard.start(body.im_type)` 返回 `{task_id}`;`im_type` 不支持时 400
  - [ ] `GET /onboard/{task_id}` → `OnboardTaskState` 序列化;不存在时 404
  - [ ] `DELETE /providers/{im_type}/{bind_id}` → `runtime.im_runtime.unbind(im_type, bind_id)` 返回 `{ok: True}`
- [ ] `protocols/im/__init__.py`:export `register_routes` / `IMRuntime` / `IMRouter` / `IMProvider` / `InboundEvent` / `OutboundContent` / `ImCredential` / `CredentialStore`
- [ ] 选做:`agent_bridge/tests/test_im_routes.py` 用 FastAPI TestClient 验证四个端点的 status code + response shape(mock im_runtime;可省略,但留个 happy path 防回归更稳)
- [ ] `./scripts/check` 全绿

**门槛**:routes 文件落地 + register_routes 形态对齐其他 protocol。

### M22.7 BridgeRuntime 装配 + app.py lifespan 集成

> 完成后 bridge 进程能正确装配 IMRuntime,启动/退出生命周期工作,没有绑定凭据时空跑不崩。

- [ ] `agent_bridge/settings.py`:加 IM 相关 settings(如 `im_credentials_dir: Path | None`,默认 `None` = 使用 `agent.user_data_dir() / "im_credentials"`;`im_resume_dir` 类似)
- [ ] `agent_bridge/assembly.py`:
  - [ ] `BridgeRuntime` 加 `im_runtime: IMRuntime | None = None` 字段
  - [ ] `build_runtime`:构造 `CredentialStore(base_dir=im_credentials_dir)` → 构造 `IMRouter(runtime, session_bridge)` → 构造 `IMRuntime(runtime, router, credentials)` → 构造 `OnboardSessionRegistry(im_runtime)` 挂在 IMRuntime 内部成员;塞到 `BridgeRuntime.im_runtime`
- [ ] `agent_bridge/app.py`:
  - [ ] `create_app_with_runtime`:加 `from .protocols.im import register_routes as register_im_routes` + 调用 `register_im_routes(app, runtime)`
  - [ ] `_make_lifespan`:`agent_runtime.start()` 之后 `if runtime.im_runtime is not None: runtime.im_runtime.start()`;退出反序 `await runtime.im_runtime.stop(timeout=5.0)`
- [ ] 验证:空绑定状态下启动 bridge,`./scripts/check` 全绿,`uv run python -m agent_bridge` 启动不崩,`GET /v1/im/providers` 返回 `[]`
- [ ] `./scripts/check` 全绿

**门槛**:bridge 进程在零绑定凭据下能正常启动 + lifespan 正常关闭 + REST 端点返回正确空状态。

### M22.8 前端 services + IMConnectDialog

> 完成后前端 IMConnectDialog 可单独渲染(mock 后端响应),不依赖 ActionBar 触发。

- [ ] `frontend` 跑 `npx shadcn@latest add dialog`,生成 `frontend/src/components/ui/dialog.tsx`
- [ ] `frontend/src/services/api/im.ts`:`listProviders` / `startOnboard(imType)` / `pollOnboard(taskId)` / `unbindProvider(imType, bindId)`(对齐 `sessions.ts` 风格)
- [ ] 安装 QR 渲染库(`react-qr-code` 轻量,或者直接显示 `qr_url` text fallback):`pnpm add react-qr-code` 或同款,加到 `frontend/package.json`
- [ ] `frontend/src/components/im/IMConnectDialog.tsx`:
  - [ ] `interface Props { open: boolean; onOpenChange: (open: boolean) => void; }`
  - [ ] dialog 主体:已绑定列表 + 接入新 IM 入口
  - [ ] dialog 内所有交互元素标 `data-hit`
  - [ ] backdrop = transparent / 不渲染(用 shadcn dialog 的 `DialogOverlay` 覆盖样式 `bg-transparent`)
  - [ ] dialog mount/open 变化时 `useEffect` 调 `listProviders`
  - [ ] 点 QQ 按钮 → 调 `startOnboard("qq")` → 拿 `task_id` → 启动轮询(250ms 周期,`qr_ready` 之后切 1s,`success/failed` 停止)
  - [ ] `qr_ready` 状态 render `<QRCode value={qr_url} />`
  - [ ] `success` → 重新 `listProviders` 刷已绑定列表
  - [ ] 已绑定条目"解绑"按钮 → `unbindProvider` + 刷列表
- [ ] `pnpm typecheck` + `pnpm lint` 全绿;dialog 在 dev server 能独立渲染

**门槛**:`pnpm typecheck` 通;mock 后端 `listProviders` 返回 `[]` 时 dialog 能正常打开 / 关闭。

### M22.9 ActionBar 加按钮 + PetApp 接 dialog 状态

> 完成后 actionbar 点击"接入 IM"能弹出 dialog,完整 UX 走通(后端可能未绑定,dialog 空状态 OK)。

- [ ] `frontend/src/pages/pet/ActionBar.tsx`:
  - [ ] `Props` 加 `onOpenIMConnect: () => void`
  - [ ] `buttons` 数组加一项 `{ icon: <Plug />, tooltip: "接入 IM", onClick: onOpenIMConnect }`(`Plug` from lucide-react)
- [ ] `frontend/src/pages/pet/App.tsx`:
  - [ ] state `const [imDialogOpen, setImDialogOpen] = useState(false)`
  - [ ] `<ActionBar ... onOpenIMConnect={() => setImDialogOpen(true)} />`
  - [ ] 在 ActionBar 之外条件渲染 `<IMConnectDialog open={imDialogOpen} onOpenChange={setImDialogOpen} />`
- [ ] `pnpm typecheck` + `pnpm lint` 全绿
- [ ] 浏览器 dev mode pet.html 跑起来:鼠标移到形象 → 操作栏浮出 → 点"接入 IM"按钮 → dialog 弹出(后端服务可能没起,dialog 空列表 OK,主要验"弹出 + 关闭"无报错)

**门槛**:dev server 内点击 actionbar 按钮能开关 dialog;dialog 内交互不被 cursor passthrough 吃掉(`data-hit` 生效)。

### M22.10 Smoke 脚本 + 自动化门禁

> 完成后 `./scripts/check` 全绿 + 独立 `./scripts/im-smoke/run.sh` 能在本机非破坏性跑通(代码层 e2e,不接 QQ gateway)。

- [ ] `scripts/im-smoke/run.sh`(bash):`set -euo pipefail` + `cd "$(dirname "$0")/../.."` + `exec uv run python scripts/im-smoke/smoke.py "$@"`
- [ ] `scripts/im-smoke/run.ps1`(powershell):`$ErrorActionPreference = "Stop"` + `$OutputEncoding = ...UTF8` + `Set-Location (Join-Path $PSScriptRoot "..\..")` + `& uv run python scripts/im-smoke/smoke.py @args`(含 BOM)
- [ ] `scripts/im-smoke/smoke.py`:
  - [ ] 用 `AGENT_FRIEND_DATA_DIR=<tmp>` 隔离测试
  - [ ] `build_runtime(settings)` → 获取 `BridgeRuntime`
  - [ ] 构造一个 `FakeIMProvider` 实例(本地最小 IMProvider 实现)注册到 `runtime.im_runtime._providers`
  - [ ] FakeIMProvider 直接调 `router.handle_inbound("fake", fake_event, capture_send)` 灌一条假 `InboundEvent(chat_id="user-abc", content="你好,我的猫叫小白")`
  - [ ] 断言:`capture_send` 收到 `OutboundContent`,`text` 非空 + `chat_id == "user-abc"`
  - [ ] 断言:`<tmp>/sessions/im:fake:user-abc.jsonl` 存在
- [ ] `scripts/README.md` 加一行表格说明(`im-smoke` 用途 + mac/win 用法 + 例外:**只跑 mac/linux**?还是两端都做)
- [ ] `./scripts/check` 全绿(三平台)
- [ ] `./scripts/im-smoke/run.sh` 本机跑通

**门槛**:`./scripts/check` 全绿 + smoke 脚本在本机一次跑通。

### M22.11 手动真跑 AC 验收(macOS)+ 收尾

> **由用户在自己机器跑**。agent 不动代码,只把 AC 逐项整理在此供用户对照真跑;跑出问题时 agent 回头修。

- [ ] **AC-1 onboard 流程**:点 actionbar 接入 IM → dialog → 选 QQ → 看到 QR → 用手机 QQ 扫码 → dialog 状态变"已绑定 Bot openid=12AB...EE26"
- [ ] **AC-2 凭据加密**:绑定后查 `~/Library/Application Support/agent-friend/im_credentials/` 下文件名是 `qq_<hash>.json.enc`,内容是 binary(明文不出现);grep 项目代码确认 app_id / client_secret 字符串不进 stdout / 日志 / 配置文件
- [ ] **AC-3 收发链路**:用另一个 QQ 账号(或自己另一台设备)私聊已绑定的 Bot 发 "hello",1-5 秒后 QQ 收到 agent 回复
- [ ] **AC-4 跨通道同一个它**:QQ 私聊 Bot "我的猫叫小白" → 等 agent 回复(确认本轮已入 memory)→ 打开桌宠对话窗问 "我的猫叫什么" → 看 agent 能答出"小白"(双向也验:桌宠说过的事 QQ 能记起来)
- [ ] **AC-5 IM 多轮连贯**:在 QQ 里跟 Bot 连续聊 5-10 轮,上下文不丢(参考桌宠多轮)
- [ ] **AC-6 重启不丢**:`Ctrl-C` 杀 agent_bridge → 重启 → QQ 里继续发消息 → IM 上下文还在(记忆 + IM session 都还在)
- [ ] **AC-7 断线重连**:断网 30 秒 → 重连网络 → QQ 里发消息能正常处理(SDK 内部 reconnect 自动)
- [ ] **AC-8 错误码兜底**:发一条"敏感词"消息(测试用,可能触发 43xxx)或主动模拟权限错误(11xxx)→ agent_bridge 进程不崩,日志能定位错误,actionbar 面板 status 字段反映 degraded/error
- [ ] **AC-9 smoke 真跑**:`./scripts/im-smoke/run.sh` 本机跑通
- [ ] **AC-10 三平台门禁**:macOS / Win / Linux `./scripts/check` 全绿(Win/Linux 真跑留下个需求,沿 015/016 同款约定)
- [ ] 全 AC 通过后:`requirement.md` / `design.md` / `progress.md` 总体状态置 `COMPLETED`
- [ ] 准备合入 main(走 PR 流程)

**门槛**:10 条 AC 全勾;`./scripts/check` 三平台全绿;用户明确确认验收完成。

---

## 实现日志

| 日期 | 完成内容 | 备注 |
|------|---------|------|
| 2026-06-17 | M22.1 依赖装配 + 模块脚手架 | qqbot-agent-sdk 1.2.2 + cryptography>=42 装齐;protocols/im/ 与 adapters/ 子目录 `__init__.py` 落地;rebase 上 origin/main 把 pre-existing lint fix 纳入 baseline |
| 2026-06-17 | M22.2 数据类型 + 凭据加密 + 单测 | event.py(re-export SDK InboundEvent)/ content.py(OutboundContent)/ provider.py(IMProvider Protocol)/ credentials.py(AES-GCM + 用户名+主机名 derive key + 原子写)+ 16 个 credentials 测试全过;./scripts/check 全绿(454 passed) |
| 2026-06-17 | M22.3 IMRouter + 单测 | router.py(IMRouter + session_id_for f"im:{type}:{chat_id}" 策略点 + handle_inbound asyncio.to_thread 跑同步 stream + TextDelta 聚合 + Conv.stream 异常走 FALLBACK_TEXT)+ 12 个 router 测试全过;./scripts/check 全绿(466 passed) |
| 2026-06-17 | M22.4 QQ Adapter + 单测 | adapters/qq.py(QQAdapter wrap qqbot-agent-sdk 4 件套 QQApiClient/QQWebSocket/EventParser/WSCallbacks · 11 个 SDK callbacks 接线 · 43xxx/11xxx 错误码 log warning · 网络异常 catch · stop 幂等 · resume token AES-GCM 同款 sha256[:16] 文件路径)+ 20 个 adapter 测试全过(全部 mock SDK,不接真 QQ gateway);./scripts/check 全绿(486 passed) |
