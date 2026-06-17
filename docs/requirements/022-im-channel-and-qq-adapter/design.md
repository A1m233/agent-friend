# 022 · IM 通道接入 + 首条 QQ Adapter — 技术方案

> 把 IM 通道作为 `agent_bridge` 的第三类 protocol 接入；首条 adapter 落地 QQ 官方 Bot OpenAPI 创建者模式（基于 `qqbot-agent-sdk`）。IM session 与桌宠 session 独立、记忆/人格通过 `owner_user_id` 跨 session 共享；session_id 由 IM user 唯一稳定决定 → 复用现有 `SessionBridge.bind_persistent` + `JsonlSessionStore`，**不动 `agent/` 核心**。IMProvider 抽象留好但本期不实装第二条 adapter。

---

## 状态

<!-- 草稿（Draft） | 已确认（Confirmed） -->
已确认（Confirmed）

## 需求文档

→ [requirement.md](./requirement.md)

---

## 1. 设计目标回顾

承接 [requirement.md](./requirement.md) §2 In Scope 共 9 项 + §4 共 10 条 AC，本设计的核心承诺：

1. **第三类 protocol**：`agent_bridge/protocols/im/` 跟 `openai/` `ag_ui/` 并列，长出 IMProvider 抽象 + 统一事件 shape + 轻量 IMRouter。
2. **不动 agent/ 核心**：IM session 走现成 `SessionBridge.bind_persistent`，session_id 由 IM user 唯一稳定决定（本期 `f"im:{im_type}:{event.chat_id}"`），落盘 / open / 复用全部 free。
3. **跨通道"同一个它"**：通过现有 `owner_user_id` 全局 + PostTurn hook 立刻 observe → 记忆跨 session 自动共享，AC-4（"我的猫叫小白"）天然成立。
4. **首条 adapter = QQ**：wrap `qqbot-agent-sdk` 4 件套（`QQApiClient` + `QQWebSocket` + `EventParser` + `WSCallbacks`），c2c 单聊文字收发，重连/Resume/心跳全靠 SDK 内部 daemon thread。
5. **凭据加密本地存储**：AES-GCM + 用户名 / 主机名 derive 密钥，跨平台一致，不依赖 OS keychain。
6. **桌面 GUI 扫码 onboard**：actionbar 加"接入 IM"按钮 → 在 pet 主窗里弹 shadcn dialog（无 backdrop，浮在 pet 形象旁）→ 选 IM 类型 → 扫码 → 完成绑定。
7. **smoke 级 AC 验证**：本机非破坏性,纯代码 e2e 灌假 InboundEvent，不接 QQ gateway，不入 `./scripts/check`（独立 `./scripts/im-smoke/run.sh`）。

---

## 2. 整体改动地图

### 新增

| 文件 | 说明 |
|---|---|
| `agent_bridge/src/agent_bridge/protocols/im/__init__.py` | export `register_routes` / `IMRuntime` / `IMProvider` / `InboundEvent` / `OutboundContent` |
| `agent_bridge/src/agent_bridge/protocols/im/provider.py` | `IMProvider` Protocol（扩展点：未来第二条 adapter 实现这个接口） |
| `agent_bridge/src/agent_bridge/protocols/im/event.py` | `from qqbot_agent_sdk import InboundEvent as _SDK; InboundEvent = _SDK`（platform-agnostic shape 直接借用） |
| `agent_bridge/src/agent_bridge/protocols/im/content.py` | `OutboundContent` dataclass（本期文本） |
| `agent_bridge/src/agent_bridge/protocols/im/router.py` | `IMRouter`：inbound → `session_id_for()` → `SessionBridge.bind_persistent` → 聚合 `TextDelta` → 回写 |
| `agent_bridge/src/agent_bridge/protocols/im/runtime.py` | `IMRuntime`：持有 providers list，start/stop 挂 lifespan（同 `AgentRuntime` 模式） |
| `agent_bridge/src/agent_bridge/protocols/im/onboard.py` | `OnboardSessionRegistry`：异步 task 注册表 + 包 `qqbot_agent_sdk.start_onboard` |
| `agent_bridge/src/agent_bridge/protocols/im/credentials.py` | `CredentialStore`：AES-GCM 加密读写凭据 |
| `agent_bridge/src/agent_bridge/protocols/im/routes.py` | FastAPI `/v1/im/*` 路由 |
| `agent_bridge/src/agent_bridge/protocols/im/adapters/__init__.py` | 占位 |
| `agent_bridge/src/agent_bridge/protocols/im/adapters/qq.py` | `QQAdapter` implements `IMProvider`，wrap `qqbot-agent-sdk` 4 件套 |
| `agent_bridge/tests/test_im_router.py` | 单测：inbound → session_id 算法 + bind_persistent 调用 + outbound 聚合 |
| `agent_bridge/tests/test_im_credentials.py` | 单测：AES-GCM 加解密 + 密钥 derive + 跨进程读取 |
| `agent_bridge/tests/test_im_qq_adapter.py` | 单测：mock `QQWebSocket` / `QQApiClient`，验证 callbacks 接线 + 错误码兜底 |
| `agent_bridge/tests/test_im_onboard.py` | 单测：mock `start_onboard`，验证 task 注册表状态机 |
| `frontend/src/services/api/im.ts` | 前端 service：`listProviders` / `startOnboard` / `pollOnboard` / `unbindProvider` |
| `frontend/src/components/im/IMConnectDialog.tsx` | shadcn dialog（无 backdrop）：已绑定列表 + 选 IM 类型 + QR 显示 + 状态轮询 |
| `frontend/src/components/ui/dialog.tsx` | shadcn `add dialog` 引入 |
| `scripts/im-smoke/run.sh` + `run.ps1` | 本机 smoke 测试入口（不接 QQ gateway） |
| `scripts/im-smoke/smoke.py` | smoke 脚本本体：启动 bridge → 灌假 InboundEvent → 断言 outbound |

### 改动

| 文件 | 说明 |
|---|---|
| `agent_bridge/src/agent_bridge/assembly.py` | `BridgeRuntime` 加 `im_runtime: IMRuntime \| None`；`build_runtime` 装配 `CredentialStore` + `IMRouter` + `IMRuntime` |
| `agent_bridge/src/agent_bridge/app.py` | lifespan 在 `agent_runtime.start()` 之后 `im_runtime.start()`，退出反序；`create_app_with_runtime` 加 `register_im_routes(app, runtime)` |
| `agent_bridge/pyproject.toml` | 新增 `qqbot-agent-sdk[qrcode]>=1.2.2` + `cryptography>=42` 依赖（cryptography 可能已经传递依赖,确认时补） |
| `frontend/src/pages/pet/ActionBar.tsx` | `buttons` 数组加一项 `{icon: <Plug />, tooltip: "接入 IM", onClick: onOpenIMConnect}` |
| `frontend/src/pages/pet/App.tsx` | state 加 `imDialogOpen`；条件渲染 `<IMConnectDialog open={imDialogOpen} onOpenChange={setImDialogOpen} />` |
| `frontend/package.json` | shadcn dialog 引入（`@radix-ui/react-dialog`） |

### 严格不动（plumbing freeze · 回归门槛）

- `agent/`：任何文件不动（Session / memory / runtime / persona / context / prompts / sessions / 工具链全部复用）
- `agent_bridge/session_bridge.py`：`bind_persistent` 一行不改
- `agent_bridge/protocols/openai/*`、`agent_bridge/protocols/ag_ui/*`、`agent_bridge/push/*`：不动
- `agent_bridge/routes/meta.py`：不动
- `memory/`：不动
- 015/016/017 plumbing：push 通道 / bubble window / pet overlay / actionbar paging 逻辑全部不动

---

## 3. 架构决策

### 3.1 IM 是第三类 protocol，长在 `agent_bridge/protocols/im/`

跟 `protocols/openai/` / `protocols/ag_ui/` 并列，但跟它们的**差别**：openai / ag_ui 是 HTTP inbound（路由模式），IM 是 agent_bridge **主动出去连**外部 gateway（长连出站模式）。

→ 沿用 `AgentRuntime` 的生命周期模板：`BridgeRuntime.im_runtime: IMRuntime | None` + `assembly.build_runtime` 装配 + `app.py` lifespan `start()` / `stop()`。HTTP 部分（onboard 触发 + 状态查询）通过 `register_routes(app, runtime)` 挂 `/v1/im/*`，跟 `push` module 一样。

### 3.2 IM ↔ agent 衔接 · 复用 `SessionBridge.bind_persistent`

```python
# router.py 核心逻辑
class IMRouter:
    def session_id_for(self, im_type: str, event: InboundEvent) -> str:
        """本期实装：每个 IM user 一个独立的、永久复用的 session。

        未来切'IM 跟桌宠共享 session' / '按时间切' / '按主题切' = 重写这个 method（subclass override）。
        """
        return f"im:{im_type}:{event.chat_id}"

    async def handle_inbound(self, im_type, event, send_fn):
        session_id = self.session_id_for(im_type, event)
        boot = PersistentBootstrap(
            thread_id=session_id,
            new_user_input=event.content,
            default_persona=self._runtime.default_persona,
            default_model=self._runtime.default_model,
        )
        try:
            text = await asyncio.to_thread(self._run_turn_sync, boot)
        except Exception:
            logger.exception("IM turn 跑挂(session=%s)", session_id)
            text = FALLBACK_TEXT  # "我现在有点问题，稍后再试"
        await send_fn(OutboundContent(
            chat_id=event.chat_id,
            chat_scope=event.chat_scope,
            text=text,
            reply_to_message_id=event.message_id,
        ))

    def _run_turn_sync(self, boot: PersistentBootstrap) -> str:
        """跑一轮 Conversation，聚合所有 TextDelta 到完整文本。

        Conversation.stream 是同步 generator（design 006 §4.4），
        所以整体在线程池跑（asyncio.to_thread）。
        """
        conv = self._bridge.bind_persistent(boot)
        buf: list[str] = []
        for ev in conv.stream(boot.new_user_input):
            if isinstance(ev, TextDelta):
                buf.append(ev.text)
            elif isinstance(ev, TurnDone):
                break
            # ToolCallRequest/Result：agent 主链路自处理，IM 不感知
        return "".join(buf)
```

**关键决策**：

- **不流式分片**：IM 端聚合 `TextDelta` 整段回复（requirement §3 已锁），QQ c2c 没有打字态，分片只是切碎，UX 不一定更好。
- **session_id_for 是 IMRouter 的 method**：未来切换策略 = subclass override，是面向"路由策略热替换"的最自然形态；模块级函数反而要 monkey-patch import，测试更难（这是 declare 6 的决定）。
- **错误兜底固定文案**：`Conversation.stream` 抛错时回固定文本 `FALLBACK_TEXT`，不走 persona 渲染——理由是这是 channel 层 fallback，语义不该过 persona 层。

### 3.3 IMProvider Protocol

```python
# provider.py
class IMProvider(Protocol):
    """An IM platform adapter (e.g. QQ / Feishu / Telegram)."""

    type: str          # 'qq' / 'feishu' / 'telegram'
    bind_id: str       # 在 IM 平台内唯一标识当前绑定（QQ = user_openid）

    def start(self, on_inbound: Callable[[InboundEvent], Awaitable[None]]) -> None:
        """启动长连进程；inbound 消息时调用 on_inbound 回调。"""

    async def send(self, content: OutboundContent) -> None:
        """回写消息给 IM 平台。"""

    async def stop(self) -> None:
        """停止长连，释放资源。"""

    def status(self) -> Literal["active", "degraded", "error", "stopped"]:
        """当前通道状态（GET /v1/im/providers 返回此字段）。"""
```

**扩展点说明**：

- **加新平台 = 新增 `adapters/<x>.py` implements IMProvider**，Router / Runtime / Onboard / Credentials / Routes 零改动
- 本期**不实装第二条 adapter 验证抽象正确性**（requirement §3 已锁），靠"QQ adapter 写出来是一份干净的 IMProvider 实现 + 未来真要加第二条时改动量小"间接证明

### 3.4 IMRuntime 生命周期 · 同 AgentRuntime 模式

```python
# runtime.py
class IMRuntime:
    def __init__(self, runtime: BridgeRuntime, router: IMRouter, credentials: CredentialStore):
        self._runtime = runtime
        self._router = router
        self._credentials = credentials
        self._providers: dict[tuple[str, str], IMProvider] = {}  # (type, bind_id) → adapter

    def start(self) -> None:
        """加载所有已绑定凭据 + 启动每个 provider 长连。"""
        for cred in self._credentials.list_all():
            self._spawn_provider(cred)

    async def stop(self, timeout: float = 5.0) -> None:
        for p in list(self._providers.values()):
            try:
                await asyncio.wait_for(p.stop(), timeout=timeout)
            except asyncio.TimeoutError:
                logger.warning("IM provider %s/%s stop timeout", p.type, p.bind_id)

    def register_after_onboard(self, cred: ImCredential) -> None:
        """onboard 完成后立即建 provider 实例启动（不需要重启 bridge）。"""
        self._credentials.save(cred)
        self._spawn_provider(cred)

    def list_status(self) -> list[ProviderStatus]:
        return [
            ProviderStatus(type=p.type, bind_id_masked=_mask(p.bind_id), status=p.status())
            for p in self._providers.values()
        ]

    def _spawn_provider(self, cred: ImCredential) -> None:
        provider = self._build_provider(cred)
        on_inbound = self._make_inbound_cb(provider)
        provider.start(on_inbound=on_inbound)
        self._providers[(provider.type, provider.bind_id)] = provider

    def _build_provider(self, cred: ImCredential) -> IMProvider:
        if cred.im_type == "qq":
            return QQAdapter(cred)
        raise ValueError(f"unsupported IM type: {cred.im_type}")

    def _make_inbound_cb(self, provider: IMProvider):
        async def cb(event: InboundEvent) -> None:
            await self._router.handle_inbound(
                provider.type, event,
                send_fn=lambda c: provider.send(c),
            )
        return cb
```

**Lifespan 挂载**（`app.py`）：

```python
@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    if runtime.agent_runtime is not None:
        runtime.agent_runtime.start()
    if runtime.im_runtime is not None:
        runtime.im_runtime.start()
    try:
        yield
    finally:
        if runtime.im_runtime is not None:
            await runtime.im_runtime.stop(timeout=5.0)
        if runtime.agent_runtime is not None:
            runtime.agent_runtime.stop(timeout=5.0)
        runtime.close()
```

### 3.5 QQ Adapter · wrap qqbot-agent-sdk 4 件套

```python
# adapters/qq.py
class QQAdapter:
    type: str = "qq"

    def __init__(self, cred: ImCredential):
        self._cred = cred
        self.bind_id = cred.user_openid
        self._api: QQApiClient | None = None
        self._ws: QQWebSocket | None = None
        self._parser = EventParser()
        self._on_inbound: Callable | None = None
        self._status: Status = "stopped"
        self._resume_path = _resume_token_path(cred)

    def start(self, on_inbound):
        self._on_inbound = on_inbound
        self._api = QQApiClient(app_id=self._cred.app_id, secret=self._cred.client_secret)
        gateway_url = self._api.get_gateway_url_sync()
        main_loop = asyncio.get_event_loop()  # bridge 主 loop
        callbacks = WSCallbacks(
            on_message_event=self._on_message,
            on_connected=lambda: self._set_status("active"),
            on_disconnected=lambda: self._set_status("degraded"),
            on_fatal_error=lambda code, msg: (logger.error("QQ fatal: %s %s", code, msg), self._set_status("error")),
            get_session=self._load_resume_token,
            set_session=self._save_resume_token,
        )
        self._ws = QQWebSocket(callbacks=callbacks, log_tag=f"QQBot:{self.bind_id[:8]}")
        self._ws.start(gateway_url, main_loop)

    async def _on_message(self, event_type: str, raw: dict) -> None:
        event = self._parser.parse(event_type, raw)
        if event is None:
            return
        if event.chat_scope != "c2c":  # 本期只 c2c
            return
        if self._on_inbound is not None:
            await self._on_inbound(event)

    async def send(self, content: OutboundContent) -> None:
        from qqbot_agent_sdk import MessageToCreate
        msg = MessageToCreate(content=content.text, msg_type=0, msg_id=content.reply_to_message_id)
        try:
            resp = await self._api.post_c2c_message(content.chat_id, msg)
        except Exception:
            logger.exception("QQ post_c2c_message 失败")
            return
        code = resp.get("code", 0)
        if code != 0:
            logger.warning("QQ post_c2c_message 非零 code=%s msg=%s", code, resp.get("message"))
            # 43xxx（内容审核）/ 11xxx（权限）：日志清楚，不抛、不崩

    async def stop(self) -> None:
        if self._ws is not None:
            await self._ws.async_stop()
        if self._api is not None:
            await self._api.close()
        self._set_status("stopped")

    def status(self) -> Status:
        return self._status

    # ---- internals ----
    def _set_status(self, s: Status) -> None:
        self._status = s

    def _load_resume_token(self) -> tuple[str | None, int | None]:
        if not self._resume_path.exists():
            return (None, None)
        try:
            data = json.loads(self._resume_path.read_text())
            return (data.get("session_id"), data.get("last_seq"))
        except Exception:
            return (None, None)

    def _save_resume_token(self, session_id: str, last_seq: int) -> None:
        self._resume_path.parent.mkdir(parents=True, exist_ok=True)
        self._resume_path.write_text(json.dumps({"session_id": session_id, "last_seq": last_seq}))
```

**关键决策**：

- **SDK 自带独立 daemon thread + 重连 + 心跳 + Resume**：QQAdapter 只提供 callbacks，**不自己写重连**。
- **Resume token 落简单 json 文件**（`user_data_dir / "im_resume" / f"qq_{bind_id_hash}.json"`），SDK 通过 `get_session/set_session` callbacks 读写——重启后能续上断线前的事件流。
- **错误码兜底**：`post_c2c_message` 返回 dict 里查 `code`，非零仅 log warning，不抛、不影响后续消息。
- **chat_scope filter**：只处理 `c2c`，group / guild / dm 全部 ignore（本期范围）。

### 3.6 凭据加密 · AES-GCM + 机器绑定密钥

```python
# credentials.py
@dataclass(frozen=True)
class ImCredential:
    im_type: str
    bind_id: str
    app_id: str
    client_secret: str
    user_openid: str
    extra: dict[str, Any] = field(default_factory=dict)  # 平台 specific 扩展位

class CredentialStore:
    def __init__(self, base_dir: Path):
        self._base = base_dir
        self._key = self._derive_key()

    @staticmethod
    def _derive_key() -> bytes:
        material = f"{getpass.getuser()}:{socket.gethostname()}:agent-friend-im"
        return hashlib.sha256(material.encode()).digest()

    def save(self, cred: ImCredential) -> None:
        path = self._path_for(cred.im_type, cred.bind_id)
        plain = json.dumps(asdict(cred)).encode()
        iv = os.urandom(12)
        ct = AESGCM(self._key).encrypt(iv, plain, None)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(iv + ct)

    def list_all(self) -> list[ImCredential]:
        if not self._base.exists():
            return []
        creds: list[ImCredential] = []
        for p in self._base.glob("*.enc"):
            try:
                creds.append(self._load(p))
            except Exception:
                logger.warning("failed to load credential at %s, skipping", p)
        return creds

    def delete(self, im_type: str, bind_id: str) -> None:
        self._path_for(im_type, bind_id).unlink(missing_ok=True)

    def _path_for(self, im_type: str, bind_id: str) -> Path:
        h = hashlib.sha256(bind_id.encode()).hexdigest()[:16]
        return self._base / f"{im_type}_{h}.json.enc"

    def _load(self, path: Path) -> ImCredential:
        blob = path.read_bytes()
        iv, ct = blob[:12], blob[12:]
        plain = AESGCM(self._key).decrypt(iv, ct, None)
        return ImCredential(**json.loads(plain))
```

**关键决策**：

- **base_dir = `agent.user_data_dir() / "im_credentials"`**：复用项目已有的 `platformdirs` 抽象（`agent.paths.user_data_dir()`，已 export），不引入新依赖。
- **密钥派生**：SHA-256(用户名 + 主机名 + 固定 salt)；同一台机器上 derive 出固定 256-bit key，AES-GCM 加解密。
- **换机时**：密钥不匹配 → decrypt 抛 → `list_all` 跳过 → 用户重新 onboard。可接受（requirement.md §1 已明示）。

### 3.7 Security Limitations（写明清楚）

本期凭据加密**不是端到端 hardened 方案**，存在以下已知限制：

1. **同机器其他进程能用同款公式 derive 出密钥**：任何能读 `user_data_dir / im_credentials/*.enc` 的进程，只要它能拿到当前用户名 + 主机名，就能解密凭据。
2. **不防本机 root / admin / 抢用户身份运行的攻击者**。
3. **不防 swap 文件 / 内存 dump**：凭据在 bridge 进程内存里以明文存在。

**为什么本期接受这个 trade-off**：

- 比明文落盘（spike 现状）强很多——脚本 kiddie / 简单文件 leak / git 误提交全部挡掉
- 跨平台一致，不依赖 OS keychain，避免 macOS Keychain / Win Credential Manager 双端 plumbing 负担
- agent-friend 是 local-first 个人助手，**威胁模型是"避免凭据被普通文件分享/git 误提交泄露"，不是"防本机持有者的高级攻击者"**

**未来 hardening 路径**：切到 macOS Keychain / Win Credential Manager。本期不做。

### 3.8 Onboard 流程 · 异步 task 注册表

```python
# onboard.py
class OnboardStatus(str, Enum):
    PENDING = "pending"          # task 已创建，还没拿到 QR
    QR_READY = "qr_ready"        # QR URL 已拿到，等用户扫码
    SUCCESS = "success"          # 绑定完成
    FAILED = "failed"

@dataclass
class OnboardTaskState:
    task_id: str
    im_type: str
    status: OnboardStatus
    qr_url: str | None = None
    bind_id: str | None = None   # 完成后填脱敏 id（给前端展示用）
    error: str | None = None

class OnboardSessionRegistry:
    def __init__(self, im_runtime: IMRuntime):
        self._runtime = im_runtime
        self._tasks: dict[str, OnboardTaskState] = {}

    async def start(self, im_type: str) -> str:
        if im_type != "qq":
            raise ValueError(f"unsupported IM type: {im_type}")
        task_id = uuid.uuid4().hex
        state = OnboardTaskState(task_id=task_id, im_type=im_type, status=OnboardStatus.PENDING)
        self._tasks[task_id] = state

        asyncio.create_task(self._run_qq_onboard(state))
        return task_id

    async def _run_qq_onboard(self, state: OnboardTaskState) -> None:
        from qqbot_agent_sdk import start_onboard

        def on_qr(url: str) -> None:
            state.qr_url = url
            state.status = OnboardStatus.QR_READY

        try:
            result = await start_onboard(on_qr_ready=on_qr)
            cred = ImCredential(
                im_type="qq",
                bind_id=result.user_openid,
                app_id=result.app_id,
                client_secret=result.client_secret,
                user_openid=result.user_openid,
            )
            self._runtime.register_after_onboard(cred)
            state.bind_id = _mask(cred.bind_id)
            state.status = OnboardStatus.SUCCESS
        except Exception as e:
            logger.exception("QQ onboard failed")
            state.error = str(e)
            state.status = OnboardStatus.FAILED

    def get(self, task_id: str) -> OnboardTaskState | None:
        return self._tasks.get(task_id)
```

### 3.9 IM 接入面板 · pet 主窗内 shadcn dialog（无 backdrop）

**形态决策**（declare Q2 拍板）：

- shadcn dialog 在 pet 主窗里弹（不另开 Tauri webview，不复用 settings 窗）
- **backdrop transparent / 不渲染**（避免遮黑整屏 transparent overlay）
- dialog 主体 = 带圆角 + shadow 的 card，浮在 pet 形象旁边
- 所有交互元素（关闭按钮 / IM 选择按钮 / 解绑按钮 / 关闭区）标 `data-hit`，跟 ActionBar 同款（让 `usePetPassthrough` DOM 命中机制正常工作）
- shadcn add dialog 通过 `add-shadcn-component` skill 引入 `frontend/src/components/ui/dialog.tsx`

**Dialog 内容**：

```
┌─────────────────────────────────────────────┐
│  接入 IM                              ✕     │
│                                             │
│  已绑定                                     │
│  ┌─────────────────────────────────────┐    │
│  │ QQ Bot · openid=12AB...EE26  [解绑]│    │  ← 若已绑定列条目
│  └─────────────────────────────────────┘    │
│                                             │
│  接入新 IM                                  │
│  [ QQ ]   [ 飞书(soon) ]  [ TG(soon) ]      │  ← QQ 可点；其他禁用 disabled
│                                             │
│                                             │
│  （选 QQ 后扩出二维码 + 状态文案）          │
└─────────────────────────────────────────────┘
```

**交互流**：

1. 点 actionbar"接入 IM"按钮 → `setImDialogOpen(true)` → dialog 浮出
2. dialog mount 时调 `GET /v1/im/providers`，渲染已绑定列表
3. 用户点"QQ"按钮 → 调 `POST /v1/im/onboard/start` → 拿 `task_id` → 进入轮询 `GET /v1/im/onboard/{task_id}` → status 变 `qr_ready` 时显示 QR（用 `react-qr-code` 渲染 `qr_url`）
4. 用户扫码 → status 变 `success` → dialog 重新拉 `GET /v1/im/providers` 刷已绑定列表
5. 用户点"解绑" → 调 `DELETE /v1/im/providers/{type}/{bind_id}` → 后端 `IMRuntime.unbind(type, bind_id)`（stop provider + delete credential）→ 列表更新

### 3.10 actionbar 加按钮 · 一行

```typescript
// ActionBar.tsx buttons 数组加一项
{ icon: <Plug />, tooltip: "接入 IM", onClick: onOpenIMConnect }
```

`Plug` from lucide-react。`onOpenIMConnect` 是 PetApp 传下来的回调，等于 `setImDialogOpen(true)`。

---

## 4. HTTP 接口契约（`/v1/im/*`）

| Method | Path | 请求体 | 响应 | 用途 |
|---|---|---|---|---|
| `GET` | `/v1/im/providers` | — | `[{type, bind_id_masked, status}]` | 列已绑定 IM（dialog 初始化拉） |
| `POST` | `/v1/im/onboard/start` | `{im_type: "qq"}` | `{task_id}` | 启动一次扫码 onboard |
| `GET` | `/v1/im/onboard/{task_id}` | — | `{task_id, im_type, status, qr_url?, bind_id?, error?}` | 前端轮询 onboard 状态 |
| `DELETE` | `/v1/im/providers/{im_type}/{bind_id}` | — | `{ok: true}` | 解绑（stop provider + 删凭据） |

**轮询节奏**：前端在 `qr_ready` 之前 250ms 一次，`qr_ready` 之后 1s 一次（用户扫码窗口期），`success/failed` 停止。

**错误模型**：

- 400：`{detail: "..."}`（im_type unknown / task_id 不存在）
- 503：`{detail: "im_runtime 未装配"}`（`runtime.im_runtime is None` 时）
- 其他统一走 FastAPI 默认错误响应

---

## 5. 测试策略

### 5.1 单测（入 `./scripts/check`）

| 测试文件 | 测试点 |
|---|---|
| `tests/test_im_router.py` | `session_id_for` 算法稳定性；`handle_inbound` 调用 `bind_persistent`；`TextDelta` 聚合；异常走 FALLBACK_TEXT；`ToolCall*` 不污染 outbound |
| `tests/test_im_credentials.py` | `save → list_all` round-trip；`delete` 行为；密钥 derive 同输入同输出；wrong key 解密失败 list_all 跳过；文件名 hash 路径 |
| `tests/test_im_qq_adapter.py` | `QQWebSocket` / `QQApiClient` mock 注入；`_on_message` 只处理 c2c；`send` 错误码非零 log warning 不抛；`get/set_session` 文件 round-trip |
| `tests/test_im_onboard.py` | `OnboardSessionRegistry.start` 创建 task；mock `start_onboard` 三种结果（success / qr_only / failed）→ status 正确流转；`register_after_onboard` 被调一次 |

**不写**：bind_persistent 内部行为（属于 SessionBridge 测试范围）；agent 主链路（属于 agent 包测试范围）；qqbot-agent-sdk 内部（SDK 自带测试）。

### 5.2 Smoke 测试（不入 `./scripts/check`，独立脚本）

`scripts/im-smoke/run.sh` + `run.ps1`：

```bash
# run.sh
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
exec uv run python scripts/im-smoke/smoke.py "$@"
```

`scripts/im-smoke/smoke.py`：

```python
"""IM 通道 smoke 测试（非破坏性，不接 QQ gateway）。

启动 BridgeRuntime → 注入一个 FakeIMProvider 到 IMRuntime →
通过 FakeProvider 灌一条假 InboundEvent → 用 mock send_fn 接住 OutboundContent →
断言文本非空 + session 已落盘。
"""

# 简略逻辑：
# 1. build_runtime() with AGENT_FRIEND_DATA_DIR=tmp
# 2. 替换 im_runtime 为只持有 FakeIMProvider 的实例
# 3. FakeIMProvider 主动调 IMRouter.handle_inbound 灌 event
# 4. assert outbound 文本不为空 + tmp / sessions / "im:fake:user-abc.jsonl" 存在
```

**为什么不接真 QQ**：

- 真接 QQ 要求本机已 onboard + 用户主动配合发消息 → 不是 CI 友好
- 真接 QQ 会污染绑定的 Bot（多发测试消息给自己）
- 链路本身在 spike 已经验过

**开发期手测**：本机已 onboard 后，跑 bridge 起 → 在 QQ 里给自己 Bot 发消息 → 看日志确认。**不入 AC、不入文档**，但 design 这里登记说明开发期还要走一遍。

### 5.3 三平台门禁（AC-10）

- macOS / Win / Linux：`./scripts/check` 全绿（含 IM 单测）
- macOS：端到端 AC-1~AC-9 真跑过（含 actionbar 弹 dialog、扫码、收发、跨通道记忆验证、重启不丢、断线重连、错误码、smoke）
- Win / Linux：真跑验证留下个需求，沿 015/016 同款约定

---

## 6. 影响分析

### 6.1 上下游影响

| 模块 | 影响 |
|---|---|
| `agent/` | **零影响**（不动任何文件，session/memory/persona/runtime/工具链全复用） |
| `agent_bridge.SessionBridge` | **零影响**（bind_persistent 不改） |
| `agent_bridge.BridgeRuntime` | 加 `im_runtime: IMRuntime \| None` 字段；其他 routes 零影响 |
| `agent_bridge.app.lifespan` | 加 `im_runtime.start()/stop()` 两行；其他 lifespan 行为不变 |
| `frontend/src/pages/pet/ActionBar.tsx` | buttons 数组加一项（依赖 declare 6 `Plug` icon） |
| `frontend/src/pages/pet/App.tsx` | 加 imDialogOpen state + 条件渲染 `<IMConnectDialog />`；其他 pet 行为不变 |
| `usePetPassthrough` | **零影响**（dialog 内容标 `data-hit` 即可继承现有 hit-test 机制） |
| 015/016/017 plumbing | **全部不动**（plumbing freeze 清单回归门槛） |

### 6.2 风险点

1. **`qqbot-agent-sdk` 长稳定性 / 重连真实表现**：spike 只验了 onboard + 路线 A 可行性，没真跑过 runtime 长连。IM 长跑稳定性、token 续签、断线重连边界等在 AC-7 真跑时第一次端到端 exercise。
   - **兜底**：requirement.md §3 已经把"长稳定性专项"明确划出范围；本期 AC-7 只要求"网络抖动自动重连"的基础行为，不要求 24h 长跑。
2. **`Conversation.stream` 跑在 `asyncio.to_thread` 的延时**：聚合所有 TextDelta 再回写,IM 用户感知"agent 沉默几秒后整段回复"。对单聊连续对话 UX 影响小（QQ 私聊本身就有几秒延迟感）。流式分片不在本期范围。
3. **凭据加密的限制**（§3.7）：本期接受 trade-off。
4. **shadcn dialog 在 pet 整屏 overlay 上的 cursor passthrough**：dialog 内层所有交互元素必须标 `data-hit` 才能正常点击；漏标会被 `setIgnoreCursorEvents(true)` 吃掉。设计开发期手测覆盖。

### 6.3 不在范围

requirement.md §3 全部生效。design 不重复。

---

## 7. 变更记录

| 日期 | 变更内容 | 是否需要重新实现 |
|------|---------|----------------|
| 2026-06-17 | 初稿确认 | — |

---

## 文档元信息

- **关联需求**：[requirement.md](./requirement.md)
- **关联决策**：[0001 产品愿景 · M8 IM 通道](../../decisions/0001-product-vision-and-roadmap/README.md)
- **关联探索**：[IM 通道接入](../../explorations/im-channel/README.md)
- **关联 spike**：[QQ Bot OpenAPI 链路 spike](../../../experiments/qq-bot-poc/SPIKE-NOTES.md)
