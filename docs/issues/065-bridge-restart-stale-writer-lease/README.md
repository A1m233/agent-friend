# 065 · Bridge 服务重启遗留旧进程和会话写锁

- **状态**：resolved
- **严重度**：medium
- **发现日期**：2026-09-10
- **关联需求**：121-model-connections-and-custom-models

## 现象
Windows 当前 worktree 的真实 Tauri 开发态中，原生 restart_runtime_service(service=bridge) 返回 ready，但旧 Bridge PID7836 及其 venv launcher PID53520 仍存活。新 Bridge PID51516 接管18800端口后，继续本任务新建合成会话返回 RUN_ERROR/session_io_error、inputAccepted=false，输入退回草稿。

## 根因初判
日志明确抛出 SessionWriterConflictError：会话已由另一个 Bridge 持有写入 lease。旧进程创建于01:43:40，新进程创建于01:52:27；旧 launcher 命令绑定 abb5 worktree。核对并清理两旧进程后，原会话立即成功续聊，三份历史附件读取正确。已确认阻塞来自旧进程占锁；为何服务监督器允许旧进程残留尚待定位，未证明是本需求引入，也未证明安装包存在同样行为。

## 影响与判断
服务重启后可能无法恢复既有会话。验证使用本任务合成会话 c893b889-48f5-4b1d-9819-896b36fa23ab，无删除会话或锁文件。附件协议与配置本身的恢复已验证，但不能宣称原生服务重启全链路通过。生命周期修复超出本轮模型能力边界，单独延期；不将运行态清理视为代码修复。

## 可选修复方向
核对 Windows uv/venv launcher 子进程树、优雅退出等待与强制终止、旧进程退出确认后再发布 ready。以旧进程退出、写锁释放和恢复后实际发送作为回归判据。

验证记录见需求121 artifacts/desktop-attachment-e2e-20260910.json。

修复自动化与安装验证记录见 [verification.json](verification.json)；原 case 在需求分支的复测状态见 [feature-retest.json](feature-retest.json)。

已在 commit `22e78eb7` 修复；原 case 及完整安装矩阵验证见 [feature-retest.json](feature-retest.json)。
