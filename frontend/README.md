# frontend

`agent-friend` 的**桌宠前端**模块（Tauri 前端部分）。

## 状态

**孵化期不实现，仅占位预留。**

> 孵化期不需要前端——调试入口由 `tools/` 下的 Python CLI 提供。
> 真正需要本目录是在 **Phase 1 · 形态期**（桌宠形态启动时）。详见 [`docs/decisions/0001-product-vision-and-roadmap/README.md`](../docs/decisions/0001-product-vision-and-roadmap/README.md) 第 4 节路线图。

## 未来技术栈方向

已在 [`docs/decisions/0002-incubation-tech-stack/README.md`](../docs/decisions/0002-incubation-tech-stack/README.md) 第 3.6 / 3.7 节锁定方向：

- Tauri 2
- TypeScript 5.x
- Node LTS（22+）
- pnpm

具体版本与 Web 框架（React / Vue / Svelte 等）选型，**Phase 1 启动时再正式决定**。

## 与其他模块的预期通信

未来通过 **HTTP REST + SSE** 与 Python 后端（`agent/api/`）通信。详见 0002 第 3.12 节。
