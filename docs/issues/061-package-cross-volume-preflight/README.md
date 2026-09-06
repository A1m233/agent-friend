# 061 · 跨盘打包缺少前置检查，受管 Python 发布失败

- **状态**：open
- **严重度**：medium
- **发现日期**：2026-09-06
- **关联需求**：[118-long-session-query-and-cancellation](../../requirements/118-long-session-query-and-cancellation/requirement.md)（交付时发现，不是118产品逻辑引入）

## 现象

在Windows、源码位于C盘、系统TEMP位于C盘时，执行 `scripts/package/run.ps1 all --local-test --state-dir D:/agent-friend-packaging` 并传入已验证的Guide参数。源码为 `6b23515e99d81b7241fc48e9c59a9a4b7206b9b4`，构建 `20260906T143801Z-6b23515e99d8-a525c7c7`。

磁盘预检通过，PyInstaller耗时211.9秒并成功；runtime-stage-and-smoke运行61.7秒后，`scripts/package/vendor/managed_python.py:99` 的 `staged.replace(destination)` 抛出WinError 17。源是C盘临时目录内的prepared，目标是D盘缓存。尚未执行完整frozen doctor/capability检查或生成安装包，不能把失败构建当作合格制品。

## 根因初判

已确认（源码及绑定构建的运行时证据）：`prepare()` 用不指定父目录的 `TemporaryDirectory` 在系统TEMP准备payload，再通过目录rename发布到可由state-dir指定的缓存。Windows不支持跨卷rename。

另有静态确认的后续限制：`scripts/package/run.py::_tauri_relative_path` 要求源码与打包目录同盘，否则在生成Tauri配置时才拒绝。它没有在耗时冻结之前检查；仅调整TEMP不能满足这一约束。此后续拒绝未在第一轮实际执行到，不冒充第二次实测失败。

## 影响与判断

影响Windows跨盘state override，尤其默认盘不足40GiB、按提示使用另一盘的场景；不影响118会话取消、摘要或展示协议。先延后打包器修改，保持已验收产品源码不变。

当前规避是把同一提交签出到容量充足的盘，并让源码、state和仅本构建进程的TEMP/TMP同盘。C/D两份源码的标准runner `packageInputSha256` 均为 `c129cff4fe486da10711bd1a1dc44c6bd774b496bd2ff077fc215cb48a584f73`；仍须完成实际构建门禁，指纹一致不等于包已通过。

## 可选修复方向

受管Python在目标缓存所在卷创建临时目录，再原子发布；构建前提前检查所有同盘约束，并同步state override说明。若计划支持源码与构建目录跨盘，应先核实Tauri配置边界，补齐Windows双卷回归；不能只绕过rename错误或降低磁盘/验证门槛。
