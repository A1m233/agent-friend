# 会话文件 - 进度

## 总体状态
COMPLETED

## 任务列表
- [x] 核对仓库、规则、059 缺口与诊断覆盖，确认需求和设计
- [x] 会话文件服务、模型工具与宿主绑定
- [x] 工作目录容量、输入副本与清理
- [x] 摘要来源标记与模型可见性
- [x] 自动化回归、项目检查和冻结运行时验证
- [x] 用户验收：2026-09-25 用户明确要求合入主干并推送

## AC 验证
- [x] AC-1～AC-10：服务与工具确定性测试通过；真实模型与跨平台边界见下
- [x] AC-11：项目检查与冻结验证通过；真实 LLM 验证未授权、未执行

## 实现日志
| 日期 | 完成内容 | 验证或备注 |
|---|---|---|
| 2026-09-24 | 创建并推送 feature/142-session-files 占号；落盘确认的需求和设计 | 基线 c30f2b96882dcee18dfa88abb76b116e42cd7f20；尚未提交实现 |
| 2026-09-24 | 新增 session_files list/prepare、只读独立副本、共享容量检查和删除清理 | 定向测试 71 passed / 1 skipped；26 MiB PDF 能准备且工具结果不含正文 |
| 2026-09-24 | 新增 C24-11/12 的合成路由/缺失原件行为用例与判分测试 | 派生案例非诊断包字节级回放；未调用真实 LLM；相邻 C24-10 留作后续行为 control |
| 2026-09-24 | 后端 lint、mypy 通过，开始全量 backend/frontend/rust 检查 | 检查进行中，不视为已通过 |
| 2026-09-24 | frontend 门禁通过；Rust 门禁通过 | 前端 158 文件 / 1,319 tests，生产构建成功；cargo fmt/test/release check 成功 |
| 2026-09-24 | 首轮 backend 全量 3,593 passed / 5 skipped / 2 failed | 两条失败为摘要标记和 eval 数量断言未同步，已更新；113 项相关复验通过，额外实际触发 fake compaction 后重取测试通过；最终后端全量复验进行中 |
| 2026-09-25 | 最终 backend 门禁通过 | lint、mypy（746 个文件）通过；3,599 passed / 5 skipped，退出码 0 |
| 2026-09-25 | runtime-only package-sensitive 验证通过 | 0.3.0 / local-test；build ID `20260924T154609Z-c30f2b96882d-11187284`；doctor 全部通过，Bridge/Voice 启动 smoke 通过，runner 退出码 0 |
| 2026-09-25 | 收尾核对完成 | 临时 Guide baseline 已恢复为构建前 b26c637f9a3518fa32da157462334ef9d38ad900 / snapshot 64267f659277f40277a5897bf4f6a991f30dd7ecf83213ba8a981b456de648af；没有该 build ID 的运行进程；git diff --check 通过 |

| 2026-09-25 | 用户确认交付并要求合入主干、推送远端 | 实现提交 b652533ef3041cf8a80cb2acfc4b312fd40c3cf5；已推送功能分支 |
| 2026-09-25 | 公开 Guide 更新并发布 | r66 / fdd6c9e9aa7f52bcf98bc6352108a3d97fe973ca；validator、verify 通过，immutable=true，发布资产摘要核验通过；不更新产品 builtin |

## 最终验证证据

| 验证 | 入口或证据 |
|---|---|
| 后端完整门禁 | `scripts/check/run.ps1 --stage backend`：3,599 passed / 5 skipped；第三方 LiteLLM / Bilibili 清理警告未影响退出码 |
| 前端完整门禁 | `scripts/check/run.ps1 --stage frontend`：lint/typecheck、1,319 tests、production build 通过；保留既有 CSS/chunk 提示 |
| Rust 门禁 | `scripts/check/run.ps1 --stage rust`：fmt、test、release check 通过；保留既有 dead-code 提示 |
| 压缩后重取 | `test_compacted_upload_is_recoverable_from_durable_session` 实际触发 fake LLM 压缩，断言原上传消息与附件退出模型窗口，再通过持久引用准备原字节 |
| 文件生命周期与隔离 | `agent/tests/test_session_files.py` 的 14 项测试覆盖跨轮/重启、同名不同内容、外会话引用与失活引用拒绝、缺失/同大小篡改、独立副本、容量、路径链接、Windows 保留名和只读清理 |
| 大文件 | 26 MiB PDF 字节夹具超过原 25 MiB 工作目录阈值，物化成功；工具结果只返回元数据与路径，不返回文件正文 |
| 模型工具协议 | registry 最终 wire schema、非法参数与会话覆盖拒绝；Conversation 宿主绑定测试通过 |
| 行为判分 | C24-11/12 固定结果夹具复用产品工具 schema；正确轨迹通过、同名本地替代及无证据回答轨迹失败。未进行真实模型评测 |
| 冻结验证 | `scripts/package/run.ps1 runtime --local-test --verification-level package-sensitive --guide-repo <受管 checkout> --guide-commit 578a354dbbb8cb722b5e166a476dc02c58f1f849` |
| 冻结能力结果 | `capability:session_files`：`ok=true, restart=true, verified_copy=true, readonly_cleanup=true`；命令沙箱、会话默认目录及 PDF native tools 的既有 doctor 同时通过 |
| 构建身份 | `c30f2b96882dcee18dfa88abb76b116e42cd7f20` + 当前未提交实现；dirty=true；上述 build ID 为本次精确验证对象，不是已安装产品身份 |

未构建/安装完整 installer；runtime-only 制品按标准 runner 生命周期保留。2026-09-25 用户确认交付并授权合入主干、推送远端，进入 Git 与公开 Guide 发布收尾。

## 当前验证边界

- 测试使用临时文件和 fake 输入，不读取用户上传 PDF 正文，不访问外部 LLM。
- 会话目录容量为写入前检查，不是运行中 OS 磁盘配额；任意长驻命令仍可在运行期间增加文件。
- 原件在 prepare 时校验；随后外部进程修改副本不由工具持续监控，重新 prepare 会重验并恢复。
- 自动化在 Windows 源码环境执行；macOS/Linux 和真实模型自主工具选择尚未验证。
- package-baseline 只读检查：公开 Guide r65 对应 578a354dbbb8cb722b5e166a476dc02c58f1f849，immutable=true。此次构建时未发布公开 Guide；后续用户合入并推送授权触发公开 Guide 更新工作流。
