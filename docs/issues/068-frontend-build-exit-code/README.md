# 068 · Windows 前端构建失败时检查入口可能误报通过

- **状态**：open
- **严重度**：medium
- **发现日期**：2026-10-06
- **关联需求**：154-session-scheduled-requests（验收过程中发现，非本需求新增）

## 现象

执行 `scripts/check/run.ps1 --stage frontend` 时，Vite 因读取到正在写入的 `TaskEditor.tsx` 而报 JSX 截断错误，日志明确含 `Build failed` 与 `Command failed with exit code 1`，但外层继续输出 `[ok] frontend 3/3 production build` 和 `check stages passed: frontend`，shell 返回 0。

该失败样本未作为需求通过证据；停止编辑源码后重新运行完整 frontend gate，测试 1423 项通过，Vite 明确报告成功构建。

## 根因初判

`scripts/frontend/build.ps1` 最后调用 `pnpm run build @args`，没有显式传播 `$LASTEXITCODE`。`scripts/check/run.py` 通过独立 `powershell -File` 子进程运行脚本，仅检查进程退出码；内部原生命令的失败可能被包装层吞掉。当前证据覆盖这一入口，未扫描其它脚本。

## 影响与判断

Windows 完整检查可能把失败构建误判为成功，需人工核对 Vite 输出作为临时规避。本轮工作范围是任务列表与表单，脚本行为修复暂缓单独处理；不能从本问题推断历史所有检查结果均失效。

## 可选修复方向

显式传播原生命令退出码，补充成功／失败子进程的确定性验证，并检查 Windows 和 shell 两端入口的失败语义是否一致。
