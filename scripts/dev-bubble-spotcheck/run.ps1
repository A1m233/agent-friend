# scripts/dev-bubble-spotcheck/run.ps1 — 016 bubble window dev spot-check（windows）
# 对照 mac/linux 的 run.sh —— 不依赖 bridge / 真 LLM，dev 期直接调 invoke 命令快速
# 验证 bubble window 的 size / 位置 / 翻转。
#
# 端到端真跑走 015 的 `scripts/dev-pet-bubble-demo/run.ps1`，本脚本互补。
#
# 用法：.\scripts\dev-bubble-spotcheck\run.ps1
#
# 详见 docs/requirements/016-pet-bubble-independent-window/。
$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..\..')

@"
===> 016 bubble window dev spot-check（不依赖 bridge）

启动 frontend dev 后，按下面步骤操作：

  1. 等待 tauri dev 加载完成（pet 窗 + chat 窗都出现）
  2. 右键 pet 窗 → "检查"（dev 模式下 Tauri 默认允许）打开 webview 的 devtools
     - bubble window 默认 hidden；若想直接检查它的 webview，临时改
       frontend/src-tauri/tauri.conf.json 把 bubble window 的 visible 改成 true
  3. 在任一 webview devtools console 里跑：

       // 1. show / hide
       const inv = (await import('@tauri-apps/api/core')).invoke;
       await inv('show_bubble');
       await inv('hide_bubble');

       // 2. 调 size（含 clamp 测试）
       await inv('show_bubble');
       await inv('set_bubble_size', { width: 240, height: 64 });   // MIN
       await inv('set_bubble_size', { width: 360, height: 480 });  // MAX
       await inv('set_bubble_size', { width: 100, height: 32 });   // → clamp 240×64
       await inv('set_bubble_size', { width: 999, height: 999 });  // → clamp 360×480

       // 3. 模拟主动轮气泡（不依赖 bridge）：
       const store = (await import('@/stores/petBubble')).usePetBubbleStore;
       store.setState({ phase: 'showing', current: { id: 't1', text: '测试气泡很短', sourceKind: 'cron:bedtime' } });
       store.getState().expand();
       store.setState({ phase: 'idle', current: null });

  4. Windows 上确认 bubble window 是独立 OS 级 window（任务管理器查 'agent-friend'
     看 webview 子进程）—— AC-2 的直接证据。

"@

Write-Host "===> 启动 frontend dev（pnpm tauri dev）..."
& .\scripts\frontend\dev.ps1
