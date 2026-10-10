# 158 · 构建缓存与打包工作区治理 — 进度

## 总体状态

COMPLETED（用户已确认合入主干并推送）

2026-10-10 用户明确授权开始实现；自动回收仅涉及受管缓存和中间目录，不删除安装包及其 manifest、symbols、必要证据。该授权不等于启用本机自动回收或接管历史目录。

## 任务列表

- [x] 确认需求、技术方案与原始调查证据。
- [x] 实现标准库构建存储模块及隔离测试：配置、记录、路径边界、锁、容量与回收。
- [x] 实现轻量 CLI、快速/精确盘点、配置、保留与显式接管。
- [x] 接入 package 路径、计划、生命周期、runtime-verify、多卷和兼容锁（真实隔离矩阵 14/14 与独立校验通过）。
- [x] 接入 dev、frontend、rust-check、runtime-regression 消费者（真实 Rust 检查通过）。
- [x] 同步脚本文档、项目规则、skills 与 adapter。
- [x] 完成专项、项目检查和按影响要求的真实验证。
- [x] 用户验收。

## AC 验证

- [x] AC-1～AC-2：容量策略、保护及删除重验。
- [x] AC-3～AC-4：验证、续跑、失败及回收生命周期。
- [x] AC-5：多入口和历史根接入。
- [x] AC-6～AC-7：无依赖初始化的查询与可取消扫描。
- [x] AC-8～AC-9：多卷预检及跨根并发。
- [x] AC-10～AC-11：计划、指引和现有契约回归。
- [x] AC-12：前后证据已归档；用户确认合入主干并推送。

## 实现日志

| 日期 | 完成内容 | 验证或备注 |
|---|---|---|
| 2026-10-10 | 用户授权实现，建立进度记录 | 分支 feature/158-build-cache-and-packaging-governance；尚无代码验证结论 |
| 2026-10-10 | 完成管理核心、CLI 和消费者接入 | 首轮专项 24 通过；修复导入路径对同名 native_symbols 的遮蔽以及阶段检查不必要的 Git 查询后，存储与原 package 合计 159 通过 |
| 2026-10-10 | 补充 CLI、错误恢复与 package 生命周期回归 | 新的存储与生命周期专项合计 31 通过；覆盖 cwd/参数/target 透传、无 uv 初始化查询、报告不被 finalization 擦除、验证回收与续跑 |
| 2026-10-10 | 管理入口与指引验证 | 当前 worktree 无 .venv，两个轻量状态入口成功；PowerShell 解析通过；skill validator 和 guidance audit 通过；project adapter sync/doctor 通过 |
| 2026-10-10 | 启动完整检查和真实验证前置 | 后端 lint/typecheck 通过、全量测试进行中；前端/Rust 检查进行中。installer-contract 判定 full-matrix；尚无完整矩阵通过结论 |

| 2026-10-10 | 前端和 Rust 全量检查完成 | 前端 178 文件、1471 测试通过，lint/typecheck/build 通过；Rust Tauri Core test + release check 通过，耗时 294 秒 |
| 2026-10-10 | 后端首轮结果作废并重跑 | 在测试进程运行期间修改共享模块，造成已加载模块与后续导入的新文件不一致，28 个 ImportError；未作为通过证据。收齐改动后专项 99 通过，修正格式后冻结代码，重新运行完整后端检查 |
| 2026-10-10 | 隔离 fixture 盘点对比 | 4 对象 / 4000 文件 / 4096000 逻辑字节，热文件系统，7 次采样；递归中位 11.66ms，快速 10.18ms。未声称大目录同等加速或物理空间减少，见 benchmark JSON |

| 2026-10-10 | 冻结代码后的后端完整检查通过 | lint / format 985 文件、mypy 811 文件通过；pytest 4052 passed / 5 skipped / 8 warnings，467.37s。完整 backend stage 553.4s。现存第三方退出清理 warning 不影响 exit 0 |
| 2026-10-10 | 启动真实 installer-smoke candidate 构建 | Guide r87（immutable=true），commit d019c3579b83e6fb14aa219f29cc033a22b63074；使用 D:/agent-friend-build-validation/158 独立 state/releases。策略 automaticCategories 为空，没有启用真实自动回收 |

| 2026-10-10 | 真实跨根互斥验证通过 | candidate 构建持有锁时，alternate-state 的预检退出 1：build resource is busy: 4aa1f81a6164.lock。未启动第二次构建 |
| 2026-10-10 | 首次真实 candidate 在 vendor 发布处失败 | 精确 build 20261009T174246Z-2de2b4bded82-15499d63；归档保留 WinError 17：C 盘临时目录不能原子重命名到 D 盘 cache；父进程随后被 GBK 错误文本触发 UTF-8 解码失败。Guide overlay 已恢复，两个受管对象标 failed 并保留 48h，空间 reservation 已释放 |
| 2026-10-10 | 补齐多卷 vendor 发布与 UTF-8 子进程协议 | Codex 和 managed Python 在目标 cache 卷创建临时目录；child Python 固定 UTF-8 输出。新增回归连同相关测试 85 通过；正在通过标准 check 入口重跑 lint/typecheck 与全部 package/storage 测试 |

| 2026-10-10 | 共享 Guide checkout 漂移被门禁拒绝 | 默认受管 checkout 被更新到 r88；其 reviewed_product_commit 不是当前分支祖先。本轮延续首次构建前已核验的 r87，在 D:/agent-friend-build-validation/158/guide-r87 建立精确 detached checkout，verify 17 文件通过；未切换公开基线或改共享 checkout |
| 2026-10-10 | 第二个 candidate 冻结及能力验证通过，配置阶段暴露同盘假设 | build 20261009T175917Z-2de2b4bded82-8879c920；doctor、bridge/voice 和 frontend 成功，随后 Tauri relative path 拒绝 D: 对 C:。两个 vendor 跨盘发布已通过 |
| 2026-10-10 | 移除 Tauri 配置同盘限制并验证解析 | 同盘保持相对路径，跨盘使用 Windows extended filesystem path，避免 FrontendDist 的 URL-first 反序列化将盘符当作 URL。本机 tauri-utils 2.9.2 编译探针确认 Directory + absolute + exists；标准 check 的 lint/typecheck 和全部 package/storage 171 测试通过（69.4s） |

| 2026-10-10 | 第三个 candidate 暴露 Tauri 资源盘符归一化缺陷 | build 20261009T182035Z-2de2b4bded82-5c20189c；frozen doctor/capability 与 bridge/voice 通过，Tauri 资源枚举丢弃 Windows drive prefix。未发布安装包，不计矩阵通过 |
| 2026-10-10 | 增加临时同卷资源视图 | payload 保留在 D 盘受管对象，Tauri 经 C 盘临时 junction 读取；退出时校验精确链接和目标后只移除链接。真实 tauri-utils 2.9.2 探针枚举 metadata 1 文件、payload 7541 文件；NSIS 保留 lexical alias，清理后 payload 完整 |
| 2026-10-10 | 最新专项检查通过 | 标准 scripts/check/run.ps1 --stage backend scripts/package scripts/build-storage -q：175 passed / 1 warning；lint/format 987 文件与 mypy 811 文件通过，完整阶段 43.4s。此前完整后端、前端及 Rust 结果仍保留其精确范围 |
| 2026-10-10 | 诊断被中断后的现场恢复 | 核对 owner PID 63068 与相关 Python/Cargo/rustc/NSIS/Node 进程已退出；通过 reconcile 处理 operation 249fa1c89ce9446f888020fb66068f38，保留失败现场与 48h 保护；只移除已核对的 tauri-y18y5low/build junction，未删除 payload |
| 2026-10-10 | 恢复单独封装诊断 | 使用上述保留构建，仅验证最新资源适配器；Tauri release 编译成功，NSIS 进行中。此旧 identity 产物不作为最终验收 candidate；仍需当前代码完整构建与隔离矩阵 |

| 2026-10-10 | 跨盘 Tauri/NSIS 单独诊断通过 | tauri-bundle-diagnostic-resumed.log：Tauri exit 0，NSIS installer 生成且 embedded frontend 完整性通过；此旧 identity 仅作适配器诊断，未发布制品。开始当前代码完整 candidate 构建 |

| 2026-10-10 | 当前代码完整 candidate 构建通过 | build 20261010T043140Z-2de2b4bded82-ddade15c，0.4.1 local-test / installer-smoke；冻结 199.8s、runtime 组装与 smoke 354.6s、frontend 75.6s、helper 107.1s、Tauri/NSIS 775.8s。native symbols 与制品发布到独立 releases 根成功 |
| 2026-10-10 | 安装矩阵首次尝试被环境预检拒绝 | Codex MSIX 对 LocalAppData 新目录写入重定向；发生在任何安装和 smoke 清理之前。通过项目现有 Explorer launcher 启动同用户普通进程，原 verify_smoke_environment 探针通过，session 1，并确认两端看到同一 control/records；没有覆盖 AppData 或削弱检查 |
| 2026-10-10 | 完整隔离矩阵和独立校验通过 | 标准 installer-smoke 14/14；installer-smoke-verify exit 0。精确 packageInputSha256=1f46c87642997b6a0648070c7de29f5def0d4fc01567bf3355275ce204ca5bdb；安装包 SHA-256=0d934d8ea7a9fbc2b06041dab01a3f84c44ca389fb7353c213d7f091600d60b1 |
| 2026-10-10 | 成功生命周期与证据保留核对完成 | candidate work 与 registry 已删除，安装包/manifest/SHA/private symbols 重新校验通过；归档仍保留 timings/runtime verification/identity/profile/Guide；reservation 和资源 junction 均为空，Guide overlay 已恢复 |
| 2026-10-10 | 机器验证收口 | artifacts/20261010-validation-summary.json 记录环境、报告路径/hash、14 场景和限制；自动回收仍关闭，未接管或清理历史缓存，未执行 production 升级或语言 UI 手验。代码未提交、未合入；等待用户验收 |

## AC 证据对应

- AC-1～AC-2：build-storage 确定性测试覆盖容量/低水位、保护、归属、链接、计划后状态变化与删除失败；真实活动构建 GC preview 为 0 候选。
- AC-3～AC-4：package 生命周期测试覆盖纯验证回收、精确续跑及失败证据；真实矩阵成功后 candidate work 删除而报告保留；中断操作经进程树核对后 reconcile。未额外重复执行一次完整 runtime-verify 构建。
- AC-5：各标准入口已接入，CLI 透传/归属有测试，真实 rust-check 与 package 通过；没有为此额外启动桌面 dev 或 runtime-regression 长驻实例。
- AC-6～AC-7：无本地 .venv 的轻量查询、不可用 uv fixture、快速只读盘点、取消扫描测试与固定规模 benchmark。
- AC-8～AC-9：卷需求合并与预留测试、真实 C 源码/D 构建及制品成功、跨 state root 锁冲突拒绝。
- AC-10～AC-11：用途计划与指引校验、完整项目检查及后续 175 项受影响检查、冻结 doctor/服务 smoke、14 项安装矩阵与独立输入校验；详见 evidence-index E-009～E-021。
- AC-12：证据及 SHA 清单已齐备，用户已于 2026-10-10 确认合入与推送。小型热缓存 benchmark 不外推大目录收益，不宣称 C 盘已回收多少物理空间。

| 2026-10-10 | 用户验收并授权交付 | 用户明确要求“合入主干推送远端”，AC-12 完成；进入最新 main 同步、Guide 复核与正常 merge/push，保留既有精确候选的验证范围 |

| 2026-10-10 | 同步最新主干并完成集成检查 | 实现提交 ea2dd24c；无冲突合入 origin/main df78ccbe，集成点 5cb8d74c。主干增量为 Skill 传输修复；完整 backend lint/format 988 文件、mypy 812 文件通过，pytest 4085 passed / 5 skipped / 8 warnings，stage 479.6s，exit 0。前端/Rust 实现未变化，沿用此前检查 |
| 2026-10-10 | merge-main-and-push Guide 复核无需更新 | 公开 checkout 8e74f247、reviewed_product_commit 85f4240b 均已核对，clean、origin exact、verify 通过；基线到集成代码仅新增开发构建治理，不改变公开产品使用、用户数据或安装后行为；无受影响 reference，不刷新 builtin 或发布新 Guide |
| 2026-10-10 | 固定验收摘要的跨平台字节身份 | JSON 摘要固定 LF，并重算 MANIFEST.sha256，防止 Git 换行归一化使证据 hash 漂移；仅文档/属性变更。原始机器日志与制品 hash 保持不变 |
| 2026-10-10 | 保留验证范围并准备主干交付 | 相对已验收实现，安装器、Tauri 和打包代码未改变；旧矩阵仍绑定原精确 candidate，不宣称其覆盖后续主干 Skill 内容。此次只做 Git 合入/推送，不因合并重新产包 |
