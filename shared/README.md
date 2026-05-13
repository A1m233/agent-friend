# shared

`agent-friend` 的**跨模块共享层**。

## 定位

存放被多个模块复用的内容：

- 跨模块的类型定义 / 数据模型（pydantic 模型等）
- 跨模块共享的常量
- 跨语言协议（未来 Python ↔ TypeScript 前端通信时的 schema）

> 不放业务逻辑。任何"行为"应放在对应的业务模块（`agent` / `memory` / `llm_providers`）。

## 状态

孵化期按需创建。如果某个类型只在一个模块内使用，应放在该模块内部，**不要默认就放 shared**。

## 边界

- 不依赖任何其他业务模块（位于依赖图最底层）
- 被任何业务模块依赖

## 未来扩展

当 `frontend/` 启动时，本模块可能需要导出 JSON Schema 或类似的契约描述，供 TypeScript 端生成对应类型。具体方案在 Phase 1 启动前评估。
