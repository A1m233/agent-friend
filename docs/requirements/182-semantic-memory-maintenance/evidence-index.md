# 182 · 证据索引

本索引保存普通 Semantic 正式维护接入的现状、验证边界和后续待核问题，不替代 requirement.md、design.md 或 progress.md。

## 当前综合判断

- 基线 d377ee2278854abcf40a6ff24aafb7299c0e9a70 已有普通事实数据、变更组、部分事务和检索过滤能力，但默认工具目录没有全量检查及定向维护工具。
- maintain-af-memory 的正式资源存在；只有资源不能补足宿主能力。
- 历史隔离实验及 2026-11-22 独立检查包使用临时注入工具；这些结果仅证明对应独立环境，不构成正式产品验收。
- 本需求已确认范围、方案与 v1 文本；正式 factory/Conversation/Bridge 已接入，正在完成项目门禁和冻结验证，真实模型质量与用户验收尚未完成。
- 用户本次转述的 AF 回复没有取得对应原始运行 trace，不能据此单独确认该次模型选择或实际构建身份。正式能力缺口由基线源码和历史实验入口共同支撑。

## 证据账本

以下源码锚点均以基线 commit 为准，读取日期为 2026-11-22。

| ID | 主张与来源 | 直接观察、局限 | 证据基础 / 置信度 / 状态 |
| --- | --- | --- | --- |
| E-001 | 正式宿主缺少维护工具。agent/src/agent/tools/factory.py；agent_bridge/src/agent_bridge/assembly.py | Bridge 使用 make_default_registry；提供历史查询与会话读取，没有普通 Semantic 库存和定向维护工具。不能仅凭源码断言用户某次会话的具体模型行为。 | code-supported / confirmed / active |
| E-002 | Inspector 不是普通会话工具。agent_bridge/src/agent_bridge/routes/memory.py；app.py；frontend/src-tauri/src/owner_capabilities.rs | ordinary 列表及变更查询位于 Inspector 路由；路由注册受能力开关控制。其可访问性不能替代模型工具接入。 | code-supported / confirmed / active |
| E-003 | 历史 AF 检查使用额外能力。[170 实测记录](../170-memory-quality-optimization/artifacts/20261118-af-memory-full-audit.md)；对应私有运行脚本和回执 | 私有 run_af.py 将 audit.tools() 注入独立 Conversation；首次运行记录含 audit_page、audit_evidence、audit_detail、audit_apply。不能把库存送达等同于逐条判断或产品交付。 | code-supported + runtime-observed / confirmed / active |
| E-004 | 独立只读包可以执行限定流程。2026-11-22 分发包及包内 VALIDATION.md | 在同一基线源码下，14 项合成测试、实际 Conversation 假模型循环、命令行准备/查询和预算停止通过；模型配置解析成功。零真实 LLM 调用，未在 macOS/Linux 原生主机实跑，不能证明判断质量或安装版工具能力。 | reproduction-supported / confirmed（仅所述边界） / retained |
| E-005 | 已有 Semantic 变更可复用部分写入基础。memory/src/memory/extraction/reconciler.py；store/sqlite_store.py；store/schema.py | 抽取通过事务核对旧目标，再写入 retired/introduced 成员；重复观察有提交记录。现有接口面向抽取，不能把它直接当作已获用户授权的维护接口。 | code-supported / confirmed / active |
| E-006 | 现有跨层路径不适合本需求直接复用。memory/src/memory/search/structured_source.py；facade.py | 通用结构化查询会查 Episodic 并展开旧经历引用；invalidate_sources 会影响 Episodic 和 Semantic。普通层维护必须避免这些跨层副作用。 | code-supported / confirmed / active |
| E-007 | 现有权限能力可作为接入依据。agent/src/agent/approvals/models.py、broker.py、automatic.py | 已有一次性请求、会话审批模式、动作指纹、审批版本和反馈路径。尚未定义普通记忆维护的动作载荷与授权边界。 | code-supported / confirmed / active |
| E-008 | 检索有状态过滤与派生索引回填机制。memory/src/memory/search/structured_source.py；search/vector.py；facade.py | 关键词查询和向量候选回查可过滤退役/删除状态，抽取完成后请求索引回填。维护写入如何使用相同机制仍待设计及验证。 | code-supported / confirmed / active |

## Artifact 映射与隐私

- 历史实验的源码、回执和私有原始材料保留在原 170 私有证据档案中，不复制真实记忆与会话到本需求目录。
- 独立分发包：af-memory-audit-kit-20261122.zip。
- 分发包 SHA-256：26e557bc3dd878b12df12405125a061c52307f457cb2a12d64ca4f7341929b70。
- 分发包包含代码、合成测试、启动提示词和验证说明，不含真实用户数据库、聊天、凭据或原始模型输出；已归档到本需求的项目外私有证据位置，由需求负责人提供。
- 独立包的生产源码基线与包内未提交 harness 必须分开记载；包的内容摘要是 harness 锚点，不能将其改写为生产 commit。
- 引用聊天和当前会话属于调查来源，公开文档仅保留可独立理解的结论及代码关系，不复制会话全文或用户记忆。

## 待解决问题

1. 普通层全量清单与逐条判断怎样持久化，并在来源、目标或会话变化后正确续接。
2. 如何把维护批次、目标版本、用户授权和实际回执绑定，复用既有审批机制且不重复逐条询问。
3. 错记撤销如何保留审计关系，同时避免历史检索继续将它作为曾经正确的事实。
4. 后台抽取、维护事务和索引之间有哪些竞态；需要哪些确定性反例覆盖。
5. 正式 Skill、工具实际投影和冻结运行时如何取得同一版本的验收证据。

## 对需求与设计的影响

- E-001 至 E-004 支撑正式入口验收与测试装置边界，关联 AC-01、AC-11、AC-12。
- E-005 至 E-008 支撑修改一致性、来源隔离、授权与检索核验范围，关联 AC-03、AC-04、AC-06 至 AC-10。
- 具体接口、表结构、授权载荷和测试实现由已确认的 design.md 决定；本轮实现与验证状态见 progress.md。

## 对账记录

2026-11-22：已核对基线、正式工具装配、Skill 资源、历史实验与独立包边界；本轮没有真实模型调用、用户数据修改或产品代码实施。

2026-11-22（正式接线后）：用户确认 v1 后完成产品实施；旧记录描述的是当时调查阶段。现行源码基线为 `6763e6cdc082e00188506ac94bf95606e3934058` 加本需求工作区 overlay。E-001、E-007、E-008 的缺口已在本候选中实现并完成定向机制验证，尚不表示已发布或完成真实行为验收。

| ID | 新证据 | 边界 |
| --- | --- | --- |
| E-009 | 正式 registry/Conversation、NotifyingSessionStore 与 Bridge assembly 的定向集成测试；工厂无临时工具注入。 | 固定假模型，只证明接线、分页、授权和事务，不证明质量判断。 |
| E-010 | 项目外 `prompt-v1/` 保存已确认完整候选与 diff；`applied-v1/verification.json` 验证正式工具定义及顺序完全一致，两份打包 reference 仅按仓库要求规范化 CRLF → LF，无字句或顺序差异。 | 动态 scope ID、包装摘要及明确的换行差异按实际运行保留，完整输入摘要见 progress.md。 |
| E-011 | C29 四个派生/相邻场景及真实 SQLite fixture，loader/runner/judge 的确定性验证已通过。 | 合成数据，不是原事故 trace；真实运行待单次授权。 |
| E-012 | 新增 frozen doctor capability 的源码执行通过，检查隔离数据库中的审批、改写、幂等、来源和资源读取。 | 冻结产物执行及完整三阶段检查结果由 progress.md 记录，不把源码 smoke 等同于 frozen。 |

项目外本轮证据还包括 `check-plan.json`、`check-backend.log`、`check-frontend.log`、`check-rust.log`、`check-maintenance-final.log` 和经 Release asset digest 验证的 `guide-release-manifest.json`。原始日志和模型输入不复制到公开文档。
