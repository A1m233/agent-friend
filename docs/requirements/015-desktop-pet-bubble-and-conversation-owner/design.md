# 015 · 桌宠气泡与前端对话 Owner — 技术方案

> 对应 [`requirement.md`](./requirement.md)。本文讲"怎么做"：Rust 侧 push channel 订阅器、tauri event 桥接到 pet webview、pet 气泡 store + policy + UI 组件、跨 Space 浮动 Tauri 配置、sessionProjection 防御、dev CLI 端到端联调。
>
> 项目级技术栈（Tauri 2 + React 19 + Zustand + Tailwind 等）已在 [`010 design`](../010-desktop-shell-and-chat-ui/design.md) 锁定；AG-UI 事件类型与 push envelope schema 已在 [`014 design`](../014-engine-main-loop-and-bridge-push/design.md) §3.1 / §8.2 锁定。本文只讲增量。

---

## 状态

<!-- 草稿（Draft） | 已确认（Confirmed） -->
已确认（Confirmed）

---

## 1. 设计目标回顾

把 014 已定稿的 bridge push 通道**在桌面端消费侧封口**，让 bedtime / idle reflection 这类主动轮真正"出现"在桌宠头上。三个关键取向贯穿全文：

- **Additive over breaking**：chat 窗对话端到端通路（`useConversationStore.send` → `runAgentStream` → `applyAguiEvent` reducer → MessageList）**一行不动**。所有新增模块都落在新文件，老路径只在 sessionProjection 处补一条注释（被动跳过 `system_trigger` 已经天然 work）。
- **分通道分关注点（思路 A）**：chat 窗继续走 pull（保 user 触发轮流式逐字低延迟）；pet 窗只走 push channel 并通过 `kinds=agent_turn` 订阅时就过滤掉 user_turn，**没有"去重"概念**——两条通道关注点不交叉。
- **Rust 侧持有 push 长连接 + tauri event 广播**：push channel SSE 长连接由最贴近 OS 的 Rust 持有，不依赖任何 webview 生命周期。pet webview 通过 tauri event listener 接事件、各自跑 store / policy / UI。**不引入 hidden background webview、不让 chat 窗作 owner**。

---

## 2. 整体改动地图

```mermaid
flowchart LR
  subgraph bridge["agent_bridge (既有, 014 已落)"]
    PULL["/ag-ui/run<br/>(pull, 既有)"]
    PUSH["/push/subscribe?kinds=agent_turn<br/>(SSE, 014 已落)"]
  end
  subgraph rust["src-tauri (本期新增 Rust 模块)"]
    SUB["push_subscriber<br/>(reqwest SSE + tokio task)"]
  end
  subgraph chat["chat webview (现状, 零改动)"]
    STORE_C["useConversationStore<br/>(走 pull, 现状)"]
    UI_C[MessageList]
  end
  subgraph pet["pet webview (本期新增 TS 模块)"]
    LISTEN["tauri event listener<br/>(agent://push)"]
    POLICY[policy.routeEnvelope]
    STORE_P["usePetBubbleStore"]
    UI_P[PetBubble 组件]
  end

  PULL -.fetch SSE.-> STORE_C
  STORE_C --> UI_C
  PUSH -.reqwest SSE.-> SUB
  SUB -.tauri::emit_to "pet".-> LISTEN
  LISTEN --> POLICY
  POLICY --> STORE_P
  STORE_P --> UI_P
```

涉及文件清单：

| 文件 | 改动类型 | 说明 |
|---|---|---|
| `frontend/src-tauri/src/push_subscriber.rs` | **新文件** | reqwest SSE 客户端 + tokio task；解析 envelope 后 `emit_to("pet", "agent://push", env)` |
| `frontend/src-tauri/src/lib.rs` | 修改（additive） | setup 阶段 spawn push_subscriber task；新增 `bridge_base_url()` 解析（dev 走 `http://127.0.0.1:18800`） |
| `frontend/src-tauri/Cargo.toml` | 修改 | 加 `reqwest = { version = "0.12", features = ["stream", "json"] }`、`futures-util` |
| `frontend/src-tauri/tauri.conf.json` | 修改 | pet 窗加 `"visibleOnAllWorkspaces": true`；macOS 高 windowLevel 通过 Rust setup 调 NSWindow（见 §8.2） |
| `frontend/src/stores/petBubble.ts` | **新文件** | `usePetBubbleStore`（zustand）+ tauri event 监听器；当前显示的 bubble 状态机 |
| `frontend/src/stores/petBubblePolicy.ts` | **新文件** | `PushPolicy` 接口 + `defaultPolicy`（第一版规则） |
| `frontend/src/stores/petBubblePolicy.test.ts` | **新文件** | policy 行为单测 + AC-3 "可替换" 验证 |
| `frontend/src/stores/petBubble.test.ts` | **新文件** | store 状态机单测 |
| `frontend/src/stores/sessionProjection.ts` | 修改（注释 only） | 顶注释补一段说明 `system_trigger` 主动事件被动跳过的语义；行为零改动 |
| `frontend/src/stores/sessionProjection.test.ts` | 修改（additive） | 加一条 case：含 `system_trigger` 事件的 session 投影出的 ChatMessage 序列等于不含时（兜底 AC-6） |
| `frontend/src/components/pet/PetBubble.tsx` | **新文件** | 气泡 UI 组件（位置 / 动画 / 截断 / 内部展开） |
| `frontend/src/components/pet/PetBubble.test.tsx` | **新文件** | UI 渲染单测（用 vitest + @testing-library/react） |
| `frontend/src/pages/pet/App.tsx` | 修改（additive） | 挂 `<PetBubble />` 到 pet-stage 上方；初始化 `usePetBubbleStore` 订阅 |
| `frontend/src/types/push.ts` | **新文件** | `PushEnvelope` TS 类型（与 `agent_bridge/push/protocol.py` schema 对齐） |
| `scripts/dev-pet-bubble-demo/run.sh` + `run.ps1` | **新文件** | 双端 wrapper：等 bridge 起 + 触发 `BedtimeSource` `IdleReflectionSource` demo + 提示观察桌宠 |
| `scripts/README.md` | 修改 | 登记 1 个新脚本 |

**不动什么**（要在 AC-8 显式验证）：

- `frontend/src/services/stream.ts`（`runAgentStream` 完全不动）
- `frontend/src/stores/conversation.ts`（chat 窗 store 不动）
- `frontend/src/stores/conversationReducer.ts`
- `frontend/src/pages/chat/*`
- `frontend/src/pages/pet/usePetPassthrough.ts`（透明区穿透机制不动，气泡只通过 `data-hit` 接入）
- `agent_bridge/*`、`agent/src/agent/*`（消费侧 only）
- vite proxy 配置（push 订阅由 Rust 直连 `127.0.0.1:18800`，不经 vite）

---

## 3. 架构决策

### 3.1 Owner 形态：Rust 侧 vs chat 窗 vs hidden webview

[`desktop-completeness`](../../explorations/desktop-completeness/) §4.3 (a) 列了三种 owner 形态。本期选 **Rust 侧**：

| 形态 | 优点 | 缺点 | 决断 |
|---|---|---|---|
| **Rust 侧持有 push 长连接** | 不依赖任何 webview 生命周期；两个 webview 对称；Tier 1 未来"bridge 重连"自然归属同处；push channel 本来就是常驻 SSE，最贴近 OS 的 Rust 持有最自然 | Rust 侧新增订阅 + 广播代码；每个事件经一次 IPC 序列化（开销可接受） | **选这个** |
| chat 窗作 owner（emit_to pet） | 改动最少（基本是 chat 窗 store 旁加 broadcast） | 主从耦合：pet 窗依赖 chat 窗；用户先打开过 chat 窗 webview 才存在的隐性时序耦合（虽然现状 `visible: false` 启动配置让 chat webview 全程 alive，但语义上"另一窗口要先成立"是脏的） | 不选 |
| hidden background webview | TS only 不动 Rust | 多一个 webview 实例（内存开销）；架构清晰度上像 hack；没把"应该有跨 webview 中介"这个根本问题解决干净 | 不选 |

> **关键取舍 · "TS reducer 不搬到 Rust"**：[`desktop-completeness`](../../explorations/desktop-completeness/) §4.3 (a) 在 "Rust 侧 owner" 选项下提过"要把 TS reducer 搬一层"的担忧——本设计**不搬**。Rust 侧只做"订阅 → 解码 envelope → tauri emit"三件事，业务逻辑（policy / store / UI）全在 TS 侧。reducer 本身也不复用——主动轮气泡的渲染规则跟 chat 窗 MessageList 不同（chat 窗按消息聚合，气泡是单条 turn 替换），各自跑各自的 store 反而更清晰。

### 3.2 思路 A · 分通道分关注点

`/push/subscribe` endpoint 支持 `kinds` 参数过滤（[014 design](../014-engine-main-loop-and-bridge-push/design.md) §8.4），本期 Rust 订阅时显式带 `kinds=agent_turn`：

- chat 窗：继续走 `/ag-ui/run` pull SSE（现状），消费 user 触发轮、保留流式逐字低延迟
- pet 窗：通过 Rust 订阅 push channel 但**只接 `agent_turn`**，user_turn 服务端就过滤掉了 → 桌面端**没有任何机制要做去重**

**为什么不选思路 B（全部走 push）**：

- chat / pet 关注点完全不交叉（user 触发轮永远走 chat、主动轮永远走 pet），"single source of truth" 在本期没有实际价值
- 思路 B 会让 chat 流式响应经一次 Rust IPC 中转，可能轻微抖动（chat 窗用户在场，对延迟最敏感）
- 014 push channel 镜像 user 触发流的能力（M14.6 `accept_kinds` 含 `user_turn`）**保留着没被废**，未来要做"chat / pet 真同源"（如桌宠跟着 chat 对话动嘴）只需 pet 订阅时改 `kinds=agent_turn,user_turn` 并补 policy，**架构无侵入**

> **关键取舍 · 第一版只用 `kinds=agent_turn`**：极简、自然零去重；未来切到"双 kind 订阅"是改一行 + 加一条 policy 规则。

### 3.3 事件流路径详图

```
agent_bridge
  └─ /push/subscribe?kinds=agent_turn (长 SSE)
       │
       │  event: push
       │  data: {"kind":"agent_turn", "session_id":"...", "seq":N,
       │         "source_kind":"cron:bedtime",
       │         "events":[{ "type":"assistant_message", "payload":{...} }, ...]}
       │
       ▼
src-tauri/src/push_subscriber.rs (tokio task)
  ├─ reqwest::Client::get(...).send() → bytes_stream()
  ├─ 按 SSE 帧解析 (\n\n 分隔)
  ├─ JSON 解码 → PushEnvelope { kind, session_id, seq, source_kind, events }
  └─ emit_to("pet", "agent://push", env_json)   ← Tauri IPC
       │
       ▼
pet webview (TS)
  ├─ stores/petBubble.ts: listen("agent://push", env => policy(env))
  ├─ stores/petBubblePolicy.ts: defaultPolicy(env)
  │    ├─ env.kind !== "agent_turn"  → drop
  │    ├─ env.events 中无 user-visible 内容（如 silent turn）→ drop
  │    └─ 提取 assistant 文本 → enqueue({ text, sourceKind, seq })
  └─ components/pet/PetBubble.tsx: 根据 store 状态渲染
```

---

## 4. Rust 侧：push channel 订阅器

### 4.1 模块位置 & 类型

`frontend/src-tauri/src/push_subscriber.rs`（新文件）：

```rust
use futures_util::StreamExt;
use serde::{Deserialize, Serialize};
use tauri::{AppHandle, Emitter, Manager};

/// PushEnvelope wire schema（与 `agent_bridge/push/protocol.py` 对齐）。
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct PushEnvelope {
    pub kind: String,          // "user_turn" | "agent_turn" | "heartbeat"
    pub session_id: String,
    pub seq: u64,
    pub source_kind: Option<String>,
    pub events: Vec<serde_json::Value>,  // 序列化后的 ConversationEvent 透传给 TS
}

/// 启动 push channel 长 SSE 订阅；放在独立 tokio task 跑，app 退出时随 runtime drop。
pub fn spawn_push_subscriber(app: &AppHandle, bridge_base_url: String) {
    let handle = app.clone();
    tauri::async_runtime::spawn(async move {
        if let Err(e) = run_loop(handle, bridge_base_url).await {
            log::warn!("push_subscriber 终止: {e}");
        }
    });
}
```

### 4.2 SSE 客户端实现

```rust
async fn run_loop(app: AppHandle, base: String) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let url = format!("{base}/push/subscribe?kinds=agent_turn");
    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(0))  // SSE 不限超时
        .build()?;
    let resp = client.get(&url)
        .header("Accept", "text/event-stream")
        .send().await?;
    if !resp.status().is_success() {
        return Err(format!("push subscribe failed: HTTP {}", resp.status()).into());
    }

    let mut stream = resp.bytes_stream();
    let mut buf = Vec::<u8>::new();

    while let Some(chunk) = stream.next().await {
        let chunk = chunk?;
        buf.extend_from_slice(&chunk);
        // SSE 按 \n\n 分帧（兼容 \r\n\r\n）
        while let Some(idx) = find_frame_sep(&buf) {
            let frame = buf.drain(..idx.0 + idx.1).collect::<Vec<_>>();
            let frame = &frame[..idx.0];
            if let Some(env) = parse_envelope_frame(frame) {
                if env.kind == "heartbeat" { continue; }   // heartbeat 丢弃，连接活性靠传输层判定
                let _ = app.emit_to("pet", "agent://push", env);
            }
        }
    }
    Ok(())
}

fn find_frame_sep(buf: &[u8]) -> Option<(usize, usize)> {
    // 返回 (索引, 长度)；优先 \r\n\r\n（4字节），其次 \n\n（2字节）
    // ... 实现略
}

fn parse_envelope_frame(frame: &[u8]) -> Option<PushEnvelope> {
    // 一帧里所有 "data:" 行拼成 JSON 解析
    // ... 实现略
}
```

### 4.3 生命周期：lib.rs setup 钩子

`frontend/src-tauri/src/lib.rs`：

```rust
mod push_subscriber;

pub fn run() {
    tauri::Builder::default()
        // ... 既有 invoke_handler / on_window_event 不动 ...
        .setup(|app| {
            // ... 既有 tray + cursor_feed 不动 ...
            push_subscriber::spawn_push_subscriber(app.handle(), bridge_base_url());
            apply_pet_window_level(app.handle())?;   // §8.2 补的 NSWindow level
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}

fn bridge_base_url() -> String {
    // dev：固定 127.0.0.1:18800（与 vite.config.ts proxy target 一致）。
    // 生产打包不在本期范围（参 frontend/src/constants/index.ts BRIDGE_BASE_URL 注释）；
    // 留 env 覆盖口子供未来。
    std::env::var("AGENT_FRIEND_BRIDGE_URL")
        .unwrap_or_else(|_| "http://127.0.0.1:18800".to_string())
}
```

### 4.4 错误处理边界

本期**不做**：

- **断开自动重连**：bridge 进程意外重启后 push 连接断开，subscriber task 终结、记一条 warn log，不重连。理由：requirement §3 显式把 "bridge 连接连续性" 划到下个需求；强行在本期补半套（无心跳、无指数退避）反而留半成品。
- **HTTP 5xx / 4xx 重试**：失败即终结、log warn。开发期 bridge 起来前 spawn 早一步会直接失败，dev 流程要求先启 bridge 再启 frontend（脚本 `scripts/dev-pet-bubble-demo/` 会保证顺序）。
- **背压**：reqwest stream 自然 backpressure，envelope 累积过多不是预期场景（主动轮分钟级触发频率）。

本期**做**：

- task panic 不影响 app（`tauri::async_runtime::spawn` 隔离）
- 解码失败的帧 log warn 后继续读下一帧，不退出 task
- heartbeat envelope 直接丢弃（连接活性由 TCP 层 + reqwest 维护）

---

## 5. 前端：pet 窗 store + policy

### 5.1 PushEnvelope TS 类型

`frontend/src/types/push.ts`（新文件）：

```typescript
/** 与 `agent_bridge/push/protocol.py` 的 PushEnvelope 对齐。 */
export interface PushEnvelope {
  kind: "user_turn" | "agent_turn" | "heartbeat";
  session_id: string;
  seq: number;
  source_kind: string | null;     // "cron:bedtime" / "idle_reflection" / null
  events: SessionEvent[];          // 复用现有 types/meta.ts SessionEvent
}
```

> Heartbeat 在 Rust 侧已丢弃（§4.2），TS 侧理论上看不到 `kind:"heartbeat"`，但类型保留以匹配 wire schema。

### 5.2 Policy 接口 & 默认实现

`frontend/src/stores/petBubblePolicy.ts`（新文件）：

```typescript
import type { PushEnvelope } from "@/types/push";

/**
 * 气泡出口的"事件 → 是否进气泡 + 显示什么"策略。
 *
 * 本期出口只有 pet-bubble（chat 窗不接 push channel，见 design §3.2）。
 * Policy 做成函数注入是为了后期产品迭代（"主动轮要不要也镜像到 chat 窗"
 * "勿扰模式直接丢弃" "加新出口"等）能改 policy 不动 store/UI 框架——
 * 呼应 .cursor/rules/coding-design "易变维度留扩展点"。
 */
export type BubbleItem = {
  /** 来自 envelope.seq + session_id，单调递增、用于去重 / 替换判定。 */
  id: string;
  text: string;
  sourceKind: string | null;
};

export type PushPolicy = (env: PushEnvelope) => BubbleItem | null;

const ASSISTANT_TEXT_TYPES = new Set([
  "assistant_message",  // 014 R-4.4.3 主动轮"用户可见"输出走这个 type
]);

/**
 * 默认策略：
 * 1. 只看 agent_turn（user_turn / heartbeat 直接丢；本期 Rust 订阅已过滤，TS 侧再兜底）。
 * 2. envelope.events 中无 user-visible assistant 文本 → silent turn（如 IdleReflectionSource），
 *    丢弃（R-4.4.4）。
 * 3. 拼接所有 assistant_message 的 content（按 turn 边界打包，通常 1 段；防御性兼容多段）。
 */
export const defaultPolicy: PushPolicy = (env) => {
  if (env.kind !== "agent_turn") return null;
  const texts: string[] = [];
  for (const ev of env.events) {
    if (ASSISTANT_TEXT_TYPES.has(ev.type)) {
      const content = ev.payload?.content;
      if (typeof content === "string" && content.length > 0) texts.push(content);
    }
  }
  if (texts.length === 0) return null;
  return {
    id: `${env.session_id}:${env.seq}`,
    text: texts.join("\n\n"),
    sourceKind: env.source_kind,
  };
};
```

> **关键取舍 · "判 silent 靠内容、不靠 source_kind"**：envelope 没有显式 `silent: true` 字段。最干净的判断是 "envelope.events 里有没有用户可见的 assistant 输出"——silent turn（IdleReflectionSource）按 014 设计只发 `memory_observation` 类事件、不发 `assistant_message`，所以这个判断在协议层自然成立。比硬编码 source_kind 黑名单可演进（未来再加 silent source 不用改 policy）。

### 5.3 PetBubbleStore（zustand）

`frontend/src/stores/petBubble.ts`（新文件）：

```typescript
import { create } from "zustand";
import { listen } from "@tauri-apps/api/event";
import { isTauri } from "@/utils/tauri";
import type { PushEnvelope } from "@/types/push";
import type { BubbleItem, PushPolicy } from "./petBubblePolicy";
import { defaultPolicy } from "./petBubblePolicy";

/** 气泡显示状态机。 */
export type BubblePhase = "idle" | "showing" | "expanded";

interface PetBubbleState {
  phase: BubblePhase;
  current: BubbleItem | null;
  /** 入口：策略命中则触发气泡显示；未命中则不动。新主动轮替换旧的（最简排队）。 */
  ingest: (env: PushEnvelope) => void;
  /** 用户点击气泡：展开看全文（R-4.6.1 不导向 chat 窗）。 */
  expand: () => void;
  /** 关闭气泡：手动 / 自动 timeout / 被新 item 替换。 */
  dismiss: () => void;
}

/**
 * Policy 通过依赖注入而非闭包绑死，便于测试中替换（AC-3 可替换证明 + AC-4 e2e）。
 * 默认 policy 在 store 初始化时绑定；测试可通过 setPolicy(testPolicy) 覆盖。
 */
let currentPolicy: PushPolicy = defaultPolicy;
export function setPolicy(p: PushPolicy) { currentPolicy = p; }
export function resetPolicy() { currentPolicy = defaultPolicy; }

/** 自动消失时长（ms）。第一版固定，后期可移到 user setting。 */
const AUTO_DISMISS_MS = 10_000;

export const usePetBubbleStore = create<PetBubbleState>((set, get) => {
  let dismissTimer: ReturnType<typeof setTimeout> | null = null;

  const clearTimer = () => {
    if (dismissTimer) { clearTimeout(dismissTimer); dismissTimer = null; }
  };

  return {
    phase: "idle",
    current: null,

    ingest(env) {
      const item = currentPolicy(env);
      if (!item) return;
      // 同 id 重复丢弃（极端场景兜底，envelope.seq 单调递增、Rust 侧已 dedup）
      if (get().current?.id === item.id) return;
      clearTimer();
      set({ phase: "showing", current: item });
      dismissTimer = setTimeout(() => {
        // expanded 状态下不自动消失，留给用户手动 dismiss
        if (get().phase === "showing") set({ phase: "idle", current: null });
      }, AUTO_DISMISS_MS);
    },

    expand() {
      if (get().phase === "showing") {
        clearTimer();
        set({ phase: "expanded" });
      }
    },

    dismiss() {
      clearTimer();
      set({ phase: "idle", current: null });
    },
  };
});

/**
 * 由 pet/App.tsx 启动时调用一次，建立 tauri event 订阅。
 * 返回 unlisten 函数（pet webview 生命周期不会 unmount，但保留接口对称）。
 */
export async function startPetBubbleSubscriber(): Promise<() => void> {
  if (!isTauri()) return () => {};   // web 调试下无 push 通道
  const unlisten = await listen<PushEnvelope>("agent://push", (e) => {
    usePetBubbleStore.getState().ingest(e.payload);
  });
  return unlisten;
}
```

### 5.4 第一版默认 policy 规则总览

| 输入 envelope | policy 输出 | 表现 |
|---|---|---|
| `kind: "agent_turn"`，events 含 assistant_message | `BubbleItem` | 气泡冒出 |
| `kind: "agent_turn"`，events 只有 `memory_observation` 等 silent | `null` | 桌面无反应（silent turn 正确丢弃，AC-5） |
| `kind: "user_turn"`（Rust 订阅已过滤，理论不会到 TS） | `null` | 兜底丢弃 |
| `kind: "heartbeat"`（Rust 已丢弃，理论不会到 TS） | `null` | 兜底丢弃 |

---

## 6. Pet 气泡 UI

### 6.1 PetBubble 组件

`frontend/src/components/pet/PetBubble.tsx`（新文件）：

```tsx
import { useEffect, useRef, useState } from "react";
import { usePetBubbleStore } from "@/stores/petBubble";
import { cn } from "@/utils/cn";

/** 超过此字数截断、显示"展开"按钮（R-4.6.1）。 */
const TRUNCATE_LEN = 120;

export function PetBubble() {
  const { phase, current, expand, dismiss } = usePetBubbleStore();
  const ref = useRef<HTMLDivElement>(null);
  const [flipBelow, setFlipBelow] = useState(false);

  // 屏顶贴墙时翻转到 pet-stage 下方（R-4.6.1）
  useEffect(() => {
    if (!ref.current || phase === "idle") return;
    const rect = ref.current.getBoundingClientRect();
    setFlipBelow(rect.top < 8);
  }, [phase, current?.id]);

  if (phase === "idle" || !current) return null;

  const truncated = current.text.length > TRUNCATE_LEN && phase !== "expanded";
  const displayText = truncated
    ? current.text.slice(0, TRUNCATE_LEN) + "…"
    : current.text;

  return (
    <div
      ref={ref}
      data-hit                               // 气泡区不穿透（usePetPassthrough 据此判定）
      onClick={truncated ? expand : undefined}
      className={cn(
        "absolute left-1/2 -translate-x-1/2 max-w-[280px] rounded-2xl",
        "bg-card text-card-fg px-3 py-2 shadow-lg",
        "animate-in fade-in duration-200",   // Tailwind animate-in（tailwindcss-animate plugin）
        truncated ? "cursor-pointer" : "",
        flipBelow ? "top-[180px]" : "bottom-[180px]",
      )}
    >
      <div className="text-sm whitespace-pre-wrap">{displayText}</div>
      {truncated && <div className="mt-1 text-xs text-muted-fg">点击展开</div>}
      {phase === "expanded" && (
        <button
          onClick={dismiss}
          className="mt-2 text-xs text-muted-fg hover:text-fg"
        >
          关闭
        </button>
      )}
    </div>
  );
}
```

### 6.2 与 pet-stage / 透明区穿透合一

- 气泡 DOM 加 `data-hit` 属性 → `usePetPassthrough` 的 `domHitTest()` 通过 `el.closest("[data-hit]")` 自动识别为实心区，鼠标进入气泡时 webview 接收事件、不穿透（现有机制）
- 气泡使用 `position: absolute` 相对 pet 形象定位（pet 窗 240×320，pet-stage 是 160×160 居中），气泡贴 pet-stage 上方 `bottom-[180px]` 或下方 `top-[180px]`，最大宽度 280px
- 气泡区域**不阻挡 pet-stage 拖拽**：拖拽事件由 pet-stage `onMouseDown` 触发，气泡是相邻 DOM、不嵌套在 pet-stage 内，故鼠标按下气泡时不会触发拖拽

### 6.3 动画 / 消失 / 展开

- **冒出**：Tailwind `animate-in fade-in duration-200`（项目已用 `tailwindcss-animate`，见 components/ui）
- **停留**：10 秒（`AUTO_DISMISS_MS`），到时 store 切回 `idle`，组件 unmount → fade-out 由 `data-state` 配 `animate-out` 实现（动画细节由实现期微调）
- **展开**：超过 120 字截断 + "点击展开"提示；点击调 `store.expand()` 切到 `expanded` 状态、停掉自动 dismiss 计时器、显示全文 + 关闭按钮
- **被新主动轮替换**：`store.ingest` 已处理（清旧 timer + set 新 current），UI 自然过渡

### 6.4 挂载到 pet/App.tsx

```tsx
// frontend/src/pages/pet/App.tsx (修改)
import { useEffect } from "react";
// ... 既有 import ...
import { PetBubble } from "@/components/pet/PetBubble";
import { startPetBubbleSubscriber } from "@/stores/petBubble";

export function PetApp() {
  usePetPassthrough();

  useEffect(() => {
    let unlisten: (() => void) | null = null;
    void startPetBubbleSubscriber().then((u) => { unlisten = u; });
    return () => { unlisten?.(); };
  }, []);

  // ... 既有 startDrag / openChat 不动 ...

  return (
    <div className="relative flex h-full w-full items-center justify-center bg-transparent">
      <PetBubble />
      <div className="group flex flex-col items-center gap-3">
        {/* 既有 pet-stage + 按钮，零改动 */}
      </div>
    </div>
  );
}
```

> 加一层 `relative` 容器是为了让 `PetBubble` 的 `absolute` 定位有 anchor；其余 DOM 结构不动。

---

## 7. sessionProjection 防御

### 7.1 现状已经天然兼容

`frontend/src/stores/sessionProjection.ts:30-69` 是 for 循环 + 一系列 `if (ev.type === ...)` continue 模式。任何不在已知 type 列表的事件（含 `system_trigger` / `memory_observation`）会**走完循环没匹配、自然被跳过**——这就是 R-4.5.1 要的"识别并跳过、不投影、不报错"。

### 7.2 本期工作：补注释 + 加防御测试

**代码改动**：仅在 `sessionProjection.ts` 顶注释加一段说明：

```typescript
/**
 * ... 既有注释 ...
 *
 * 关于 014 引入的 system_trigger / memory_observation 主动轮事件：
 * 这些 type 不在本投影的已知分支里，会自然跳过（不投影到 chat 窗 MessageList）。
 * 这是 015 R-4.5.1 期望的行为——主动轮 UI 出口是 pet 气泡而非 chat 窗。
 * 未来若要 chat 窗也回看历史主动轮，加一个 if 分支即可（事件已在 JSONL 保留）。
 */
```

**测试改动**：`sessionProjection.test.ts` 加一条 case：

```typescript
it("ignores system_trigger events (015 R-4.5.1)", () => {
  const baseEvents: SessionEvent[] = [/* 既有 user_message / assistant_message / tool_* 序列 */];
  const withSystemTrigger: SessionEvent[] = [
    baseEvents[0],
    { type: "system_trigger", uuid: "xxx", payload: { source_kind: "cron:bedtime" } } as SessionEvent,
    ...baseEvents.slice(1),
    { type: "memory_observation", uuid: "yyy", payload: {} } as SessionEvent,
  ];
  expect(projectSessionEvents(withSystemTrigger))
    .toEqual(projectSessionEvents(baseEvents));   // R-4.5.3 + AC-6
});
```

> `SessionEvent` 是 union 类型；这里 `as SessionEvent` 兜底，新 type 加入 `types/meta.ts` 是另一个 follow-up（不属本期范围）。

---

## 8. 跨 Space + 全屏浮动

### 8.1 tauri.conf.json 改动

```json
{
  "label": "pet",
  "url": "pet.html",
  "title": "agent-friend",
  "width": 240, "height": 320,
  "resizable": false,
  "transparent": true,
  "decorations": false,
  "alwaysOnTop": true,
  "visibleOnAllWorkspaces": true,        // ← 新增：跨虚拟桌面
  "skipTaskbar": true,
  "shadow": false,
  "fullscreen": false
}
```

`visibleOnAllWorkspaces` 在 Tauri 2 已是一等公民字段，无需 plugin。

### 8.2 全屏 app 之上浮动：Rust 侧 setup 调原生

`alwaysOnTop` 在 macOS 上对应 `NSFloatingWindowLevel`，**不够压全屏 app**。要压全屏需要 `NSScreenSaverWindowLevel`（或更高的 `kCGMaximumWindowLevel`）。

Tauri 2 没暴露这么细的 NSWindow level API（只有 `set_always_on_top` 布尔）。本期通过 `objc2` crate 直接调原生：

```rust
// frontend/src-tauri/src/lib.rs (新增)
#[cfg(target_os = "macos")]
fn apply_pet_window_level(app: &tauri::AppHandle) -> tauri::Result<()> {
    use objc2::msg_send;
    use objc2::runtime::AnyObject;
    if let Some(pet) = app.get_webview_window("pet") {
        let ns_window = pet.ns_window()? as *mut AnyObject;
        unsafe {
            // NSScreenSaverWindowLevel = kCGScreenSaverWindowLevel = 1000
            // 压过 fullscreen layer（fullscreen 是 1000 一档以下）
            let _: () = msg_send![ns_window, setLevel: 1000_i64];
        }
    }
    Ok(())
}

#[cfg(not(target_os = "macos"))]
fn apply_pet_window_level(_: &tauri::AppHandle) -> tauri::Result<()> {
    // Windows / Linux 本期不动（requirement R-4.7.3）
    Ok(())
}
```

> **依赖加 `objc2`**：项目目前 Cargo.toml 没用。这是个新依赖、需要在 design 阶段标出。`objc2` 是 `cocoa` crate 的现代继任者（cocoa 已 deprecated），社区主流选项。

### 8.3 Windows / Linux

本期 macOS only（requirement R-4.7.3）。`apply_pet_window_level` 在其他平台空实现，行为退化为单纯 `alwaysOnTop`（绝大多数场景够用，全屏游戏 / 视频例外但不在 macOS 优先级之列）。

---

## 9. Dev CLI 端到端验证

### 9.1 复用 014 dev/fire-source 触发链

014 已经在 bridge 装了 `/dev/fire-source` 端点 + `scripts/dev-fire-source/` 双端 wrapper（仅 `dev_mode=true` 挂载）。本期不重复造，只**新增一个 demo orchestrator** 把流程串起来。

### 9.2 验证脚本 & 观测路径

`scripts/dev-pet-bubble-demo/run.sh`（新文件，run.ps1 等价）：

```bash
#!/usr/bin/env bash
# 015 端到端 demo：触发主动轮后在 pet 窗气泡看见
# 前置：(1) bridge 已起（dev_mode=true）；(2) frontend 已起（pnpm tauri dev）
set -euo pipefail

echo "→ 触发 BedtimeSource（应该 ~3 秒内在桌宠头上看到气泡）"
./scripts/dev-fire-source/run.sh bedtime
sleep 6

echo "→ 触发 IdleReflectionSource（桌宠不应有任何反应、memory.observe 应被调用）"
./scripts/dev-fire-source/run.sh idle_reflection
sleep 3

echo "→ 验证：chat 窗 MessageList 应该没有这两条消息（即使打开过 chat 窗）"
echo "→ Done. 请人工核对桌宠气泡 / chat 窗 MessageList / bridge 日志。"
```

| 步骤 | 期望现象 | 对应 AC |
|---|---|---|
| 触发 bedtime | pet 气泡冒出 + chat 窗无反应 + session JSONL 有 `system_trigger` 事件 | AC-4 |
| 触发 idle_reflection | pet 气泡**不冒** + chat 窗无反应 + memory.observe 被调用（看 bridge log） | AC-5 |
| 同时正常在 chat 窗发对话 | chat 窗 MessageList 正常流式渲染 + pet 气泡**不跟随** | AC-1, AC-8 |
| 切到另一个 Space | 桌宠仍可见 | AC-7 |
| 启动全屏视频播放器 | 桌宠浮在视频之上 | AC-7 |

### 9.3 跨平台脚本约定

新增 `scripts/dev-pet-bubble-demo/run.sh` + `run.ps1`，在 `scripts/README.md` 登记。

---

## 10. 影响分析

### 10.1 上下游影响

- **chat 窗对话流**：零影响（chat 窗保持现状走 pull）
- **pet 窗形象与拖拽**：零影响（pet-stage / `usePetPassthrough` 不动；气泡是相邻 DOM）
- **sessionProjection**：零行为变更（已天然跳过 unknown type）
- **bridge / agent runtime**：零代码改动（push channel 是 014 既有产物，本期是消费侧）
- **Tauri 进程**：新增一个 tokio task（push subscriber），开销可忽略
- **bundle 体积**：新增 `reqwest` + `futures-util` + `objc2`（`reqwest` 在 Tauri app 里常见，无新增 native 依赖）

### 10.2 跨平台影响

- **macOS**：完整实现（跨 Space + 全屏浮动）
- **Windows / Linux**：核心功能（push 订阅 / 气泡 UI / 透明区穿透）跑通，跨 Space / 全屏浮动退化为单纯 `alwaysOnTop`（requirement R-4.7.3 已说明本期 macOS only）

### 10.3 风险点

| 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|
| `objc2::msg_send!` 调用 `setLevel:` 在某 macOS 版本上行为不一致 | 低 | pet 窗在全屏 app 下被遮 | spike 阶段在 macOS 14/15 各跑一次；不行降级为 1000（NSScreenSaverWindowLevel）外的更低 level 兼容性方案 |
| `reqwest` stream API 在某些 Tauri 2 + tokio 版本组合下兼容问题 | 低 | push 订阅起不来 | dev 期能立刻发现；fallback：自写 hyper client（重） |
| envelope schema 漂移（014 改了 PushEnvelope 字段） | 中 | 解码失败 | 015 design 显式声明协议契约以 014 §8.2 为准；TS / Rust 双侧加宽容字段（多/少字段 graceful） |
| 主动轮气泡显示时正好被 chat 窗遮挡（chat 窗刚好在 pet 窗位置） | 中 | 用户看不到气泡 | §8.2 NSWindow level 1000 已压过 alwaysOnTop（chat 窗 level 是默认 0），气泡在 pet 窗内是 absolute 定位、跟随 pet 窗层级 → 不会被 chat 窗遮 |
| `data-hit` 在气泡上让点击不穿透，但用户点击气泡外仍命中其他 `data-hit` 元素 | 低 | 行为不符直觉 | 气泡渲染时层级在 pet-stage / button 之上（z-index 控制）；现有 `domHitTest` 用 `closest("[data-hit]")` 已 ok |

---

## 11. 测试策略

### 11.1 既有单测预期

- `frontend/src/stores/conversation.test.ts`：**全绿不变**（chat 窗逻辑零改动）
- `frontend/src/stores/conversationReducer.test.ts`：**全绿不变**
- `frontend/src/stores/sessionProjection.test.ts`：**既有 case 全绿**，新增 1 条 case（§7.2）

### 11.2 新增单测

| 测试 | 覆盖 | 验收锚点 |
|---|---|---|
| `petBubblePolicy.test.ts: defaultPolicy 接 agent_turn 含 assistant_message` | policy 命中、返 BubbleItem | AC-4 |
| `petBubblePolicy.test.ts: defaultPolicy 拒 silent turn` | events 只含 memory_observation → 返 null | AC-5 |
| `petBubblePolicy.test.ts: defaultPolicy 拒 user_turn / heartbeat` | 兜底丢弃 | AC-1 + AC-2 |
| `petBubble.test.ts: ingest → showing → 10s 后 idle` | store 状态机 | AC-1 |
| `petBubble.test.ts: setPolicy 替换 → 不同事件可路由` | policy 可替换扩展点 | **AC-3** |
| `petBubble.test.ts: 新主动轮替换旧的（同 id 不替换）` | 多消息排队语义 | AC-4 |
| `PetBubble.test.tsx: 截断显示 + 点击 expand 看全文` | UI 行为 | AC-4 |
| `sessionProjection.test.ts: 含 system_trigger 事件投影等于不含时` | 兼容 | **AC-6** |

### 11.3 Rust 侧测试

`push_subscriber.rs` 内部 `find_frame_sep` / `parse_envelope_frame` 是纯函数，加 `#[cfg(test)]` 单测覆盖 SSE 帧解析与 envelope JSON 解码（边界：空数据 / 部分帧 / 多帧拼包）。SSE 长连接的端到端联调由 §9 dev demo 承担、不在单测里 mock。

### 11.4 端到端验收

§9.2 的人工观测表对应 AC-1 / AC-4 / AC-5 / AC-7 / AC-8 / AC-9；`./scripts/check` 全绿（含上面新增单测）对应 AC-8。

---

## 12. 变更记录

| 日期 | 变更内容 | 是否需要重新实现 |
|------|---------|----------------|

---

## 文档元信息

- **状态**：已确认（Confirmed）
- **创建时间**：2026-06-13
- **确认时间**：2026-06-13
- **承接**：本目录 [`requirement.md`](./requirement.md)
- **上游协议契约**：[`014 design`](../014-engine-main-loop-and-bridge-push/design.md) §3.1 (AgentEvent) + §8.2 (PushEnvelope)
- **下一步**：本文档确认后撰写同目录 `progress.md`（拆分实现任务）
