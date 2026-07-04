# 034 · 记忆面板隐藏后仍持续轮询 recall trace

- **状态**：open
- **严重度**：medium
- **发现日期**：2026-07-04
- **关联需求**：026-memory-inspector

## 现象

启动桌面端后，即使没有主动观察记忆面板，bridge 终端也会持续出现类似 access log：

```text
INFO:     127.0.0.1:62520 - "GET /v1/memory/recalls?limit=100 HTTP/1.1" 200 OK
```

排查时同时看到 `/healthz` 也会周期性出现；其中 `/healthz` 来自对话窗口连接态自愈，每 3 秒 ping 一次。`/v1/memory/recalls?limit=100` 则来自记忆面板 recall trace 自动刷新。

## 根因初判

`frontend/src/pages/memory-inspector/RecallTrace.tsx` 中的 `RecallTracePanel` mount 后会立即请求一次 `listRecalls(100)`，并通过 `setInterval(..., 5000)` 每 5 秒刷新。

Tauri 配置中 `memory-inspector` 是预建窗口，且窗口关闭逻辑是 hide 而不是销毁。只要该 WebView 存活，React effect 就会继续运行；当前轮询没有感知窗口可见性或页面是否正在被用户查看。

Uvicorn access logger 会把这些请求打印到当前 dev 终端，形成可见噪声。

## 影响与判断

- 不影响记忆数据正确性，也不会触发 LLM/API 调用。
- 请求本身成本较低，只是读取进程内 recall ring buffer。
- 但它会在用户没打开记忆面板时制造持续终端噪声，也会产生不必要的后台请求。
- 已确认这类 `INFO: 127.0.0.1 ... "GET /v1/memory/recalls"` access log 不会进入 `%LOCALAPPDATA%/agent-friend/Logs/*.log`：`uvicorn.access` 使用独立 handler 且 `propagate=false`，实查本地日志文件也未命中 `/v1/memory/recalls`。
- 暂不修原因：这是 memory inspector 的后台刷新策略优化，不阻塞当前主题 / showcase 相关修复。

## 可选修复方向

1. 在 Tauri 侧给 `memory-inspector` 发送 `window://shown` / hide 事件，前端只在窗口可见时启动 recall trace 轮询。
2. 前端增加 `document.visibilityState` / focus 兜底；页面 hidden 时暂停 interval，visible 时立即刷新一次。
3. 把 recall trace 自动刷新改成显式开关，默认手动刷新，用户需要实时观察时再开启自动刷新。
4. 若 access log 噪声仍影响开发体验，可单独评估是否在 dev bridge 中降低 Uvicorn access log 级别，但不要掩盖后台轮询本身。
