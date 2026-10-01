# 147 · 记忆正确性修复方案

## 状态

CONFIRMED · 2026-10-01 用户确认方案，并已授权修复既有行为。

## 边界

沿用现有 Extractor → Reconciler → SQLite 原子提交，以及活动触发的有界队列。仅修复 [需求](requirement.md) 中的五项正确性问题；证据及版本锚点由 [证据索引](evidence-index.md) 维护。

## 抽取协议

生产 Extractor 默认要求当前协议的 `core_updates`、`semantic_changes`、`episodic_entries` 三个数组。逐项验证结构，不把缺字段、错误条目或任意对象当成空结果。合法的三个空数组仍执行 observation 幂等提交。

旧 `semantic_ops` 协议只在显式启用兼容的旧 prompt / 回放入口接受；也必须具有明确的 Semantic 与 Episodic 结构。程序内历史结果对象的兼容转换保留。协议错误返回失败状态，worker 保留素材，不写成功 commit。

## 来源失效

`soft_delete_by_source_events` 对普通 Semantic 和 Core 同时检查当前及历史行的来源。保留有效期和变更图，只设置删除标记；检索及派生索引沿用现有 deleted 过滤和回填。无关会话的新事实继续有效，不自动恢复先前版本。

## 时间语义

以片段所有发言时间的最大值作为观察边界，统一为 UTC；Semantic/Core 的 `valid_from`、替换及撤回的 `valid_until`、Episodic 的 `occurred_at` 使用该边界。`created_at`、`updated_at` 与 extraction commit 保持真实写入时间。空片段仅保留已有无事实处理语义。

在 SQLite 提交事务内复核目标仍有效及时间次序。明确引用的 Semantic 目标晚于观察时间时，整批拒绝；Core 按 owner、字段及用户/persona scope 检查已有版本的起止边界，防止迟到 add 在较新 remove 后复活。多值字段保守使用字段边界，不猜测自然语言等价关系。同一观察时刻允许原子批内多值写入，不能生成反向有效期。

时间冲突使用独立错误码，整批回滚并保留为 blocked 素材。既有数据继续读取，不批量重写曾经错误的有效时间。

## 待处理记录

现有 pending 表迁移增加调度状态 `pending` / `blocked`、错误码与隔离时间；既有行默认为 pending。查询只调度 pending，冷却更新也只作用于 pending，避免覆盖 blocked 证据。原始 payload 和 source refs 保留，来源失效仍同时清除两种状态。

解码器将确定的 JSON、版本、结构和时间格式问题归为专用数据异常；worker 隔离这类记录以及确定的时间冲突，继续本次已取出的下一条。一次最多取两条，坏记录也占用名额，不无界跳过。已隔离条目不会因重启或普通提交自动重试；后续显式恢复需在修正数据后改变调度状态，不新增自动修复或 UI。

网络、认证、限流及暂时服务故障保持全局冷却和 Retry-After；未知编程错误及共享存储/Core 故障不推断为单条素材问题。flush、close、启动都不主动释放积压。

## Core 完整性

存储层将可确定的 Core JSON、值、时间及 provenance 解码异常统一转换为 `CoreProfileError`。投影先验证字段与结构，再排序；不跳过坏条目拼装部分资料。普通记忆 facade 沿用已有不可用提示和普通召回保留行为。SQLite I/O、编程错误和取消不包进数据异常。

## 验证与交付

先补五类确定性回归，再验证旧协议消费者、迁移、幂等、失败冷却、编辑失效及普通记忆降级边界。使用 fake 模型，不调用外部 LLM，不写用户记忆库。执行相关测试及项目 scripts/check 门禁，结果记录在 progress。

本次为标准 Python 逻辑与存储迁移变更，无新增运行依赖或资源，不触发安装包构建。验收仍需要用户确认；PDF 内容接入与历史资料补建不属于本次交付。

## 已确认增补：记忆响应等待

2026-10-01 用户确认小范围调整并授权合入、推送和打包。基础抽取与 Semantic 协调通过现有 ModelCallControl 显式传入记忆专用 ModelTimeoutPolicy：完整响应等待 600 秒，连接仍用默认 15 秒，写入和事件空闲仍用非流式默认 90 秒。策略只存在于调用控制中，不进入模型 JSON 或 prompt，不修改全局 completion/streaming 默认值，也不调整重试、Retry-After、冷却或 worker 生命周期。

本次不改为流式、不开放无限等待、不引入异步 provider 任务。用户同时明确要求产包，按 standard 完整构建生成 0.3.0 local-test，不覆盖既有正式版本或自动安装。
