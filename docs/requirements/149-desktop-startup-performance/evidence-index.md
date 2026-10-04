# 149 · 启动加载证据索引

## 用途与范围

服务 [需求 149](./requirement.md)，记录历史启动样本、源码机制、测量方法和后续候选。最近对账：2026-10-04。这里只归档脱敏启动元数据，不保存会话正文、凭据或完整用户日志。

## 当前综合判断

- 最新范围已由用户确认为证据采集，优化与性能门槛不再属于本次交付。
- 路由修复后的 installed-06/07 已取得有效的隔离采集、真实输入和 ZIP 证据；最终 candidate 4 的 14 项自动安装矩阵及标准报告绑定校验通过，见 E-010/E-011。项目检查全部通过，用户随后明确要求合入主干并推送，已确认验收。
- installed-04 对 installed-03 的隔离解释构成反证，旧解释保持 invalidated；后续成功不追认旧样本。见 E-009。


- 本次历史运行从进程启动到 pet 页面报告可见约 84 秒，单实例启动锁在毫秒级取得。
- 现有源码在创建桌面窗口前同步发现 packaged runtime，其中全量遍历并哈希安装清单文件。该安装清单包含 7,489 项，共 1,218,268,007 字节。
- 全量校验是有代码依据的重点假设，尚无该次运行的分段计时直接证明其占比。安装版 dirty overlay 未保留为精确源码，不能把 sourceCommit 当作完整源码身份。
- 历史日志不是受控 baseline；完整安装态 baseline/candidate 尚未执行。操作系统缓存、磁盘竞争和 WebView 初始化的贡献仍未区分。
- 后续只读 pilot 调用已安装 helper 的 `verify`：全量校验成功，整个子进程耗时 3,738.0873 ms、CPU 时间 2,156.25 ms，receipt 未变。这证明当前条件下可以低成本测量校验，不证明历史 84 秒由何造成；不能排除历史冷缓存或其他阶段等待。
- M0 已实现并验证测量入口：release 计时 helper 对同一 payload 连续 5 次校验全部通过。首轮 wall=3544.84 ms，后续四轮中位数=1203.92 ms；文件读取是本批校验的主要耗时段。没有改校验策略，不能据此声称优化收益。新的安装态采集闭环已由 E-010/E-011 补齐。

## 证据账本

| ID | 日期 | 问题或主张 | 类型及锚点 | 摘要与置信度 | 状态 | 详细记录 |
| --- | --- | --- | --- | --- | --- | --- |
| E-001 | 2026-10-04 | 本次启动在哪个区间等待 | runtime-observed；进程会话 58512-1791101236255 | 可见前约 84 秒；锁立即取得，confirmed。窗口可见事件不等于首次交互完成 | active | [历史样本](./artifacts/historical-startup.json) |
| E-002 | 2026-10-04 | 是否存在同步全量校验 | code-supported；源码 fedb4fc637da0b6d1fb171dd2e9c06811fb70429 | 调用链和逐文件哈希机制 confirmed；该机制占本次 84 秒的比例 unconfirmed | active | [调查记录](./startup-investigation.md) |
| E-003 | 2026-10-04 | 安装清单规模与构建身份 | 安装元数据；build 20261001T144253Z-87bb34d2d779-8259578d | 清单统计与 dirty 构建身份 confirmed；未重新核验所有 payload 文件 | active | [历史样本](./artifacts/historical-startup.json) |
| E-004 | 2026-10-04 | 探针是否每次需要完整打包 | code-supported；package runner 与构建契约 | 源码局部验证可独立；分步复用有身份约束，不能任意混装 | active | [调查记录](./startup-investigation.md) |
| E-005 | 2026-10-04 | 是否存在无需重建的校验入口，当前校验耗时多少 | runtime-observed；已安装 helper SHA-256 固定 | 单次 verify 成功、3.738 秒，confirmed；阶段占比和冷态表现 unconfirmed。补充 E-004 的已有入口事实 | active | [pilot](./artifacts/installed-verify-pilot.json)、[调查记录](./startup-investigation.md) |
| E-006 | 2026-10-04 | 同类产品如何区分启动阶段与首次响应 | 官方源码；openai/codex e15ecc9c354587e98c6edca2a69696d64bc55c98 | 有 phase/status/duration 的启动事件，与 turn TTFT 分开，code-supported / confirmed；不证明 AF 的瓶颈 | active | [调查记录](./startup-investigation.md) |
| E-007 | 2026-10-04 | 当前校验的分段耗时 | measured-local-baseline；release helper 6eed23718c55…，源码/runner 分别绑定 | 5 轮全量通过，首轮读取 2742.33 ms；后续 wall 中位数 1203.92 ms，confirmed；历史启动根因仍 unconfirmed | active | [基线结果](./measurement-baseline.md)、[JSON](./artifacts/verify-baseline-01.json) |
| E-008 | 2026-10-04 | M0 测量与旧日志兼容性验证 | 自动化检查与日志投影 | 后端/前端/Rust 检查通过，当前定向测试14项通过；旧日志明确缺阶段，不宣称完整；安装矩阵未执行 | active | [验证摘要](./artifacts/m0-validation.json)、[日志报告](./artifacts/historical-report-02.json) |
| E-009 | 2026-10-04 | 启动采集及诊断导出是否可用于隔离 E2E | 两个 immutable local-test build、正常/受控中断日志、实际 WebView 与 ZIP | 原生采集和 ZIP 留证 confirmed；前端混用生产端口 confirmed，原 installed-03 隔离解释 invalidated。完整矩阵未运行 | partial / verification-gap | [安装态记录](./installed-evidence.md)、[原报告投影](./artifacts/installed-sample-03.json)、[反证评估](./artifacts/isolation-assessment.json) |
| E-010 | 2026-10-04 | 路由修复后能否形成同一实例的完整采集闭环 | candidate 3，installed-05 失败 / installed-06 成功；源码 overlay 精确绑定 | bootstrap、实际请求、空会话、真实输入、ZIP 中 build/profile/hash 一致；17.83 秒为单次采样，不是优化证明 | active / bounded | [样本](./artifacts/installed-sample-06.json)、[解释](./installed-evidence.md) |
| E-011 | 2026-10-04 | 最终候选采集与安装门禁是否一致通过 | candidate 4，installed-07 / matrix-04 / 标准 verify | 17.44 秒前端终点、1.44 秒校验，采集闭环通过；14 自动场景全部 passed，报告指纹匹配；NetLog 尾部局限保留 | active / confirmed scope | [最终样本](./artifacts/installed-sample-07.json)、[矩阵](./artifacts/installer-matrix-04.json)、[解释](./installed-evidence.md) |

## 未决问题

| 问题 | 当前解释 | 最有区分力的下一项证据 | 阻塞情况 |
| --- | --- | --- | --- |
| 可见前 84 秒的分布 | 文件遍历与哈希、系统 I/O、WebView 初始化均可能贡献 | 同一实现的分阶段计时及文件读取统计 | 阻塞优化方案定案 |
| 就绪和首次操作的等待 | 后端装配、恢复、后台工作、首次查询 | 后端阶段与首次本地操作的同一启动 ID 时间线 | 阻塞端到端收益判断 |
| 冷暖状态的影响 | 历史样本缓存与磁盘负载未知 | 明确缓存条件的重复测量；未控制时标为 unknown | 不以历史样本替代正式对照 |
| 完整安装态的长阶段 | 新候选已采到完整分段；历史整体等待仍未定位 | 以后真实慢启动的同版本时间线 | 采集闭环已通过，不阻塞本次需求；历史归因留待真实慢样本 |

## 决策影响

| 决定 | 依赖证据 | 当前状态 | 重新打开条件 |
| --- | --- | --- | --- |
| 先测量分段、再选优化策略 | E-001、E-002 | 当前调查方向 | 取得决定性归因 |
| 区分局部验证与安装态验收 | E-004 | 已纳入需求 | 构建输入或安装语义改变 |
| 保留完整性失败关闭边界 | E-002、需求 077 | 需求约束 | 若方案需改变语义，重新确认 |
| 先补齐阶段证据，不预定校验缓存 | E-005 | 当前调查方向；不是已批准优化算法 | 精确阶段对照取得稳定瓶颈证据 |
| 下一步优先完整启动时间线，暂不批准具体优化策略 | E-007、E-008 | M0 后的当前判断 | 取得可比较完整基线并确认性能门槛 |

## Artifact 映射

| 证据 ID | 原始位置 | 归档位置 | 完整性 | 敏感性与访问说明 |
| --- | --- | --- | --- | --- |
| E-001、E-003 | 本机标准日志目录 tauri.log.runtime；安装目录 active-install.json、build-identity.json、payload-manifest.json | artifacts/historical-startup.json | artifacts/MANIFEST.sha256 | 白名单投影；去除用户路径、会话内容和无关事件。原日志可轮转，归档不是完整日志 |
| E-002、E-004 | 本仓库固定 revision 文件 | startup-investigation.md 中代码锚点 | Git revision/blob；无独立运行 artifact | 可复查代码，不代表已运行新候选 |
| E-005 | 一次只读 helper 调用的 stdout、退出码、单调计时、helper/receipt 哈希 | artifacts/installed-verify-pilot.json | artifacts/MANIFEST.sha256 | 参数路径脱敏；无凭据、用户内容；不能证明 helper 的精确历史源码 |
| E-006 | 官方仓库固定 commit 源码 | startup-investigation.md 的固定链接与观察 | commit 固定；未复制源码 artifact | 公开源码；非 Desktop 内部实现证据 |
| E-007 | 本机私有测量目录的 helper build 与 verify JSON | artifacts/verify-baseline-01.json | artifacts/MANIFEST.sha256 | 已剔除 helperPath，包含逐次结果、构建与 runner 源码摘要，无用户内容 |
| E-008 | 检查命令输出、本机 Rust log、原始 runtime journal | artifacts/m0-validation.json、artifacts/historical-report-02.json | artifacts/MANIFEST.sha256；摘要另记私有 Rust log 哈希 | 不归档完整带本机路径的测试日志；仅白名单日志投影 |

## 对账记录

| 日期 | 对账范围 | 结果与修订 |
| --- | --- | --- |
| 2026-10-04 | 需求、历史样本、校验源码、构建入口 | 需求已确认；优化方案、探针和正式对照尚未实施；无性能达标结论 |
| 2026-10-04 | 只读 pilot、已有 helper 入口与观测参考 | 补充 E-005/E-006；3.738 秒仅作当前条件下校验样本，维持历史根因未确认；准备测量阶段方案 |
| 2026-10-04 | M0 代码、局部基线和检查结果 | 新增 E-007/E-008；M0 实现完成但需求仍 IN_PROGRESS。性能优化、完整安装态基线与矩阵不得标为通过 |

## 最新决策（覆盖前述优化计划）

用户已确认仅交付加载时间证据采集。历史 84 秒根因、性能门槛和优化方案不再是本需求验收项。当前真正的阻塞是 installer-smoke 前端端口未隔离；保留已完成的采集实现和原生证据，暂停安装态 E2E/矩阵，等待用户决定是否单独修复路由缺陷。不得将原始 harness ok=true 当作经反证后仍成立的最终结论。

### 后续确认与 E-010

用户随后同意纳入路由修复。candidate 3 的 installed-06 已完成隔离实测；原始 ZIP、NetLog、journal、构建 overlay 保留在私有测量目录，脱敏投影及 hash 见 artifacts/installed-sample-06.json 和 MANIFEST.sha256，解释见 installed-evidence.md。正式进程保持原 PID；没有发送模型请求。

本证据只证明同一隔离实例内的采集、输入与导出闭环；单次 17.83 秒不能解释历史 84 秒。NetLog 尾部不完整已明示。完整矩阵仍待完成：修正采集器后现行 package input 指纹变化，matrix-03 预检拒绝；需要一致的新候选。此状态覆盖前述“等待用户决定修复”。

### 最终对账：E-011 覆盖此前门禁缺口

candidate 4 的 installed-07、14 项自动安装矩阵及标准 verify 全部通过。源码 overlay、候选与 ZIP 哈希、请求路由、时间线和未覆盖边界见 installed-evidence.md；新 JSON 均纳入 artifacts/MANIFEST.sha256。历史结论及失败输出保留，只有“当前缺口”被后续证据补齐。用户验收仍待确认，未提交/合入或替换正式安装。

最终后端 3813 passed / 5 skipped；前端 1367 passed；Rust test/release check 通过。检查命令、输出哈希与生命周期核对见[最终验证摘要](./artifacts/final-validation.json)。此前并行打包导致的 Guide pin 测试失败，在恢复源码基线后的完整 backend 检查中消失；没有修改断言。
