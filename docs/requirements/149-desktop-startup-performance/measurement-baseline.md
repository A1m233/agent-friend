# M0 · 校验测量基线

日期：2026-10-04。这是局部校验基线，不是优化后的安装版启动结果。

## 方法与身份

- 通过 `scripts/startup-profile/run.ps1 build-helper` 仅编译 release installer helper，没有构建 frozen Python、installer 或安装/重启用户 AF。
- 使用 `verify --repeats 5` 对既有 active payload 只读校验；重复采样前，本任务的编译、前端、Rust 和后端检查均已结束。用户其他程序及系统后台负载未受控。
- 输入清单：7,489 个文件，1,218,268,007 字节；精确 build/manifest 身份、helper hash、helper 编译时源码映射和 runner 执行时源码映射均见 [原始报告](./artifacts/verify-baseline-01.json)。helper 与 runner 分别保留锚点，报告解析增强发生在 helper 构建后，不改写历史构建来源。
- helper SHA-256：`6eed23718c55faf70cf8f21d2252fcc8945761bd20dc2533dd7cb24db387f7f0`。
- 五轮退出码均为 0，文件数、字节数和完整性验证均通过，receipt/helper/manifest/identity 的前后哈希一致。
- 首轮缓存状态 unknown；后续轮次只称为 previously-read，不声称是独立冷启动或保证 OS cache 全命中。不清空系统缓存。

## 逐次结果

单位：毫秒。metadata/read/hash 只累计 payload 文件循环；total 还含清单读取/解析、路径和身份检查等，wall 另含 helper 进程启动与退出。各列不再相加为另一份总耗时。

| 轮次 | helper wall | 校验 total | metadata | 文件读取 | SHA-256 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 1 | 3544.84 | 3413.39 | 270.70 | 2742.33 | 315.01 |
| 2 | 1222.24 | 1204.83 | 186.73 | 641.30 | 311.28 |
| 3 | 1190.86 | 1169.38 | 183.03 | 619.86 | 301.75 |
| 4 | 1216.97 | 1201.33 | 194.12 | 638.25 | 304.13 |
| 5 | 1189.03 | 1169.58 | 180.74 | 620.56 | 303.41 |

后续四轮 wall 中位数 1203.92 ms，最大值 1222.24 ms。首轮单独保留，没有从样本中删除，也没有与后续轮次混算为冷启动平均。

## 结论和局限

- 本批测量中，文件读取时间明显高于哈希计算；首轮与后续轮次的差异主要出现在读取段。可确认的是这批数据的分布，不是“所有机器瓶颈都在磁盘”。
- 当前校验循环没有采用缓存、并行化或跳过文件，因此不能把比历史问题样本快解释为优化收益。
- 不能拿 84 秒减掉本批 3.54 秒，把差值认领为 WebView 或后端耗时。历史样本没有相同阶段与缓存条件。
- helper 不经过壳的完整 receipt 发现、窗口 build/setup 或 Python 启动，因此不能代替完整安装态 baseline。
- 新增启动 phase 已通过代码测试与 release 编译，但尚未在包含这些埋点的完整桌面制品中取得实测；窗口可交互、更新后首次启动和真正冷态仍未验证。
- 历史日志经 [新版报告器](./artifacts/historical-report-02.json) 解析，明确缺少六个壳阶段和 bridge 身份就绪事件；`phaseCoverageComplete=false`、`firstInteractionVerified=false`。

## 验证

详见 [机器摘要](./artifacts/m0-validation.json)。

- 后端检查：lint、773 文件 typecheck、3809 测试通过，5 skipped；该全量进程收集时有 13 个 startup-profile 测试，后续 reporter 身份增强由当前 14 项定向测试覆盖。
- 前端检查：lint/typecheck、163 文件/1362 测试、静态生产构建通过。
- Rust 检查：309 测试通过、1 ignored、release check 通过。
- installer-contract：机器检查通过，当前路径要求 full-matrix；完整安装隔离矩阵未执行，不能据此合入或发布。
- 存在非阻断的第三方警告（CSS/大 chunk、Rust dead code、Python 依赖 warning/退出清理 warning），没有将它们列为本需求已修复项。

## 下一项有区分力的证据

用匹配身份、包含新埋点的隔离桌面制品采集一次完整启动，再按暴露出的长阶段决定是否补细分探针。优先区分 runtime-discovery（含校验与 supervisor 构造）、tauri-build/setup、service-spawn/readiness；获得同条件完整基线后再确认优化算法与阈值。
