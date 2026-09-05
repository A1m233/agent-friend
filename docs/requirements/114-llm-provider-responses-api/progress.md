# 114 · 实施进度

## 总体状态

IN_PROGRESS

2026-09-05 用户明确授权开始实现。OpenRouter 最终范围：隐藏用户入口、保留兼容代码、注释暂不维护。

## 任务列表

- [x] T1 公共 phase/capabilities 与 catalog/spec 装配；K3；OpenRouter 可见性。
- [x] T2 Responses 请求转换、原生 SDK transport 与取消。
- [x] T3 item 状态机、工具/usage/错误/diagnostics/重试归一化。
- [x] T4 deterministic tests、Chat 回归与上层接口兼容。
- [x] T5 真实 smoke 双端入口；按授权执行主力 Provider 公共接口验证。
- [x] T6 backend/frontend/rust 全量门禁与打包影响分类。
- [ ] T7 用户验收后合入 main，报告精确 main commit。

## AC 验证

| AC | 当前证据 / 缺口 |
| --- | --- |
| 1–2 | SDK HTTP mock 断言 native path/body；legacy Chat 回归；正式 smoke 证实两家原生 endpoint |
| 3 | get_model_capabilities 与 Deferred 转发测试；611 文件 mypy 通过，无旧 fake/eval 生产扩改 |
| 4–7 | 各 phase、缺失、未知、多 item 交错测试；真实 Flash 观察 final_answer，K3 观察 None；未实测 commentary |
| 8 | 单个/并行 function fake、参数/ID 拼接与真实合成工具闭环 |
| 9 | 请求转换及拒绝未支持设置测试；DeepSeek off 的 native reasoning.none 修正已补测并通过两次真实工具闭环复验 |
| 10–13 | usage/终态/失败/incomplete/EOF/取消/close/重试边界 deterministic tests |
| 14–15 | generate/complete 保留 Chat；旧 provider 回归。最新修正后 backend 全阶段 2485 passed / 4 skipped |
| 16 | 上层生产 diff 仅 Deferred 通用能力转发、设置组隐藏；Agent/Memory/展示协议无新增 Provider/API 分支 |
| 17 | frontend 689 tests + lint/typecheck/build 通过；Rust fmt/test/release check 通过；standard 包影响。最新 backend lint/typecheck/2485 tests 全绿 |
| 18 | deterministic tests 使用 HTTP 护栏；正式真实请求匹配本次四次授权，现已用尽 |
| 19 | 全 catalog 显式 route/phase 测试与证据索引；Pro/Vision 不外推 Flash 的 phase 观测 |
| 20 | Kimi K3 两次工具闭环通过；DeepSeek 修正后两次 off 工具闭环通过，共 748 tokens、两次 native reasoning_tokens=0、无推理 state；14 个正文 delta 均 final_answer。原四次报告作为历史证据保留 |
| 21 | 公开 catalog/HTTP route、设置组 SSR 测试；内部 OpenRouter 配置、校验与 Chat 兼容保留 |

详见 [证据索引](evidence-index.md)。

## 当前确认门禁

用户已确认保留原 LanguageModel 协议，改用 `get_model_capabilities(model)` 查询可选能力、缺失时 unavailable；已实施并通过类型检查，原 124 个兼容错误消除。

用户已授权并执行首次四次 smoke（共 1589 tokens）及修正后 DeepSeek 的两次单独复验（共 748 tokens），均无重试。DeepSeek off 现已由 native reasoning_tokens=0 和无 reasoning state 证明。真实调用授权额度已用尽，无继续调用计划。用户已明确确认验收及最后两项设计口径，当前仅待同步最新 main 并合入。

## 实现日志

| 日期 | 完成内容 | 验证或备注 |
| --- | --- | --- |
| 2026-09-05 | 同步最终 OpenRouter 边界；进入 Phase 3 | 生产实现前基线 1c2decdaf62de477a66f0ab9ebf0114605e5bd59 |
| 2026-09-05 | 完成原生 SDK、请求转换、item 状态机与主要归一化；补 phase/工具/取消/usage/重试测试 | guarded llm_providers + smoke harness suite 195 passed；后续 diagnostics/source 字段微调纳入全库复验 |
| 2026-09-05 | 隐藏 OpenRouter 公开模型与凭据组，保留兼容代码并注明维护状态 | frontend 全阶段通过：688 tests + lint/typecheck/build；追加设置渲染回归单测另行通过 |
| 2026-09-05 | 新增双端 responses-smoke 入口、请求次数/endpoint 护栏与脱敏错误证据 | fake 4 tests passed；`--help` 通过，未执行真实请求 |
| 2026-09-05 | 全库 Python lint 通过；mypy 待能力契约确认 | 124 errors / 21 files，均待进一步收口；backend 全量 pytest 与 rust gate 正在运行 |
| 2026-09-05 | Rust 全阶段通过 | cargo fmt、219 项测试所在 test stage 与 release check 通过；源码无 Rust 改动 |
| 2026-09-05 | 后端全量初跑 2439 passed / 22 failed / 4 skipped | 21 项为 HTTP 护栏先于 RESPX 拦截或网页 fallback 漏 mock，1 项为旧 IM 菜单仍依赖公开 OpenRouter；已调整护栏到连接创建点、补测试 fixture，复验中；上层生产代码未扩改 |
| 2026-09-05 | 最新 Responses + smoke harness 定向验证 | 43 passed；覆盖新增输入拒绝、incomplete item 与 completed 冲突、迭代等待取消 |
| 2026-09-05 | HTTP 护栏与回归测试修订后复验通过 | `voice_bridge/tests/integration/test_acceptance_criteria.py`、IM voice client、IM control、web fetch 共 51 passed；新增 guard 自检后的 Responses + harness 共 44 passed；全库最终 pytest 仍待能力契约收口后重跑 |
| 2026-09-05 | 当前 diff 打包影响为 standard | 运行时依赖与 uv.lock 无变化；新增 responses 为静态 Python 包，既有 spec collect_all(llm_providers) 覆盖，OpenAI Responses 在锁定 SDK 中用静态 import 可达；无新资源/原生库/动态加载/打包配置变化，不启动安装包构建 |
| 2026-09-05 | 能力契约兼容调整完成；类型检查与专项验证通过 | 611 文件 mypy；47 项查询/Responses/harness 测试 |
| 2026-09-05 | 首次公共接口真实 smoke | 03:28:59–03:29:13 UTC；4 次请求、1589 tokens；两家工具闭环通过，但 DeepSeek 思考 off 语义未正确生效；原始脱敏报告保留，不改写为最新代码验证 |
| 2026-09-05 | 核对官方参数并修正转换 | DeepSeek thinking.disabled→reasoning.none；Kimi/DeepSeek 不支持或会被忽略的可选配置本地拒绝；harness 补 native reasoning token=0 与无推理 state 断言；64 项专项测试通过 |
| 2026-09-05 | 三段项目门禁 | 修正前 backend 全绿 2468 passed / 4 skipped；frontend 689 passed + build；Rust 已通过。参数修正后 backend 再次运行 |
| 2026-09-05 | 最新参数修正后 backend 全阶段通过 | lint、611 文件 mypy、2485 passed / 4 skipped；保留第三方 bilibili_api 退出回调 warning，进程退出码 0；无未结束测试进程 |
| 2026-09-05 | DeepSeek off 真实复验通过 | 用户单独授权最多两次，实际两次、748 tokens；原生 endpoint、工具闭环、native reasoning_tokens=0、无推理 state、final_answer 全部通过；报告 deepseek-off-smoke-2026-09-05.json |
| 2026-09-05 | 用户明确确认验收通过 | 确认私有 ResponsesRoute 与 EOF 错误分类口径；进入最新 main 同步与 merge commit 合入流程，不额外推送远端 |

## 续行位置

用户验收已完成；当前进行最新 main 同步与合入。最新 main 为 3d62404c2207c2d2307be262c7133c7c05b4d53b，相对已验证基线只有独立聊天展示与 107 文档变更，不涉及 114 的 Provider 生产路径；合并后复验受影响前端。
