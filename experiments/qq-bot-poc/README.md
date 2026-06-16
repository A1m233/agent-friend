# qq-bot-poc · QQ Bot OpenAPI 链路可行性 spike

> **这是 spike，不是正式实现。** 唯一目标：用最低代价验证"agent-friend 通过 QQ 官方 Bot OpenAPI 直连，本地（macOS）就能完成 C2C 私聊收发"是否可行。
>
> **状态：未开跑。** 验证完即写入 `SPIKE-NOTES.md` 并决策下一步。

## 背景

详细对话见上游探索。简而言之有两条候选路线让 agent-friend 接 QQ：

- **路线 A · QQ 官方 Bot OpenAPI**（本 spike 的验证对象）
  - 合规、不封号；个人主体只需实名即可拿到 AppID/Secret
  - 截图里见到的"该机器人仅供创建人使用，暂不支持进入群聊"模式跟我们"只人对人"的需求完美契合
  - **关键不确定性**：wiki 提到"新增机器人默认启用 IP 白名单"，但语境说的是"提审和上线"门禁。实际本地直连 WebSocket gateway 是否被拦没有官方明确说法——必须实测
- **路线 B · NapCat（OneBot 协议端）** —— 灰色但稳，扫码登录用户自己的 QQ 号；如果路线 A 撞墙就退回这条

OpenClaw / Hermes 这类第三方 agent app 跟 agent-friend 是**同品类 peer**，不是 dependency；它们底下也是 wrap 了 [`qq-botpy`](https://github.com/tencent-connect/botpy) 这套官方 SDK，所以 spike 直接用 `qq-botpy` 验证最干净。

## 验证清单

跑通本 spike，等价于回答这四个问题：

1. **AppID/Secret 能否从本地（动态家庭 IP / NAT）直连 QQ Bot WebSocket gateway？**
   - `on_ready` 触发 = 通过；连接被拒 / 401 / IP 不在白名单 = 不通过
2. **C2C 单聊事件 (`on_c2c_message_create`) 能否被推送到本地？**
   - 用另一个 QQ 账号给 Bot 发私聊，本地能 print 出来 = 通过
3. **`post_c2c_message` 能否回复成功？**
   - 对方 QQ 看到"echo: ..."回复 = 通过
4. **整条链路在不依赖任何公网 endpoint 的情况下能否完成？**
   - 不需要 ngrok / 不需要部署服务器 / 不需要在管理端填任何 IP = 通过

**全过** → 路线 A 对 agent-friend 成立，进入正式集成阶段
**任一不过** → 记录失败现象到 `SPIKE-NOTES.md`，退回路线 B（NapCat）

## 运行方法

### 1. 准备凭据

```bash
cp .env.example .env
# 编辑 .env，填入从 QQ Bot 开放平台获取的 AppID / AppSecret
```

> ⚠️ 不要把 AppSecret 贴到对话 / commit 里。`.env` 已被根目录 `.gitignore` 兜底忽略。

### 2. 在 QQ Bot 管理端确认事件订阅

进 [QQ Bot 管理端](https://q.qq.com/) → 选中你的 Bot → 开发设置 → 确保"**消息事件**"或"**单聊消息**"已订阅。
botpy 的 `Intents(public_messages=True)` 只是客户端侧过滤器，**服务端事件订阅没开则永远收不到推送**。

### 3. 跑

```bash
cd experiments/qq-bot-poc
./run.sh
```

`run.sh` 会自动 `uv sync` + `uv run python src/echo_bot.py`。

### 4. 测试

用另一个 QQ 账号（不是 Bot 创建者本人也行，加 Bot 为好友后）给 Bot 发任意私聊。控制台应该看到：

```
[ready] robot 「<bot 名>」 connected — gateway OK，IP 白名单未拦
[c2c] from openid=... content='你好'
[c2c] reply sent
```

并且对方 QQ 收到 `echo: 你好`。

### 5. 额外验证：`qqbot-agent-sdk.start_onboard()` 扫码绑定流程

发现两条 SDK 后追加的实测脚本（`src/onboard_test.py`），用于验证"扫码即可拿到 AppID/Secret"这条 UX 是否真的可用。

```bash
uv run python src/onboard_test.py
```

不需要 `.env`——这个脚本就是要看 SDK 能不能从空开始拿到凭据。预期看到：

```
[onboard] ✅ QR URL 已拿到：https://q.qq.com/qqbot/openclaw/connect.html?task_id=...
[onboard]    用 QQ / 微信扫码完成绑定，等结果返回...
[onboard] ✅ 拿到凭据（已 mask，不泄明文）：
  app_id        = xxxx...xxxx (len=10)
  client_secret = xxxx...xxxx (len=32)
  user_openid   = xxxx...xxxx (len=32)
```

凭据在脚本里只 mask 后输出，不落盘。**这条流程跑通是 SDK 选型走 `qqbot-agent-sdk` 路线的关键证据，详见 `SPIKE-NOTES.md`。**

## 失败排查指引

- **启动报 IP 不在白名单 / 401** → 验证清单 #1 失败 → 这是路线 A 的 deal-breaker，立即终止 spike，退回路线 B
- **`on_ready` 触发但 `on_c2c_message_create` 不响应** → 大概率是 QQ 管理端事件订阅没开；二查 intent 名是否拼对
- **回调触发但 `post_c2c_message` 报错** → 看错误码：43xxx 通常是消息内容审核问题，11xxx 是权限问题，文档 `bot.q.qq.com/wiki/` 第 11 节有错误码表

## 验收后

把实测结论（含截图 / 错误日志）写到本目录的 `SPIKE-NOTES.md`，给出"路线 A / B"的二选一决策建议。
