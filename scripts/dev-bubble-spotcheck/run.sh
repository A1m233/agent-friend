#!/usr/bin/env bash
# scripts/dev-bubble-spotcheck/run.sh — 016 bubble window dev spot-check（mac/linux）
#
# 不依赖 bridge / 真 LLM，**只为了让你在 dev 期能快速调 bubble window 的 size /
# 位置 / 翻转**：起 `pnpm tauri dev` 后，从 bubble webview 的 devtools console 直接
# 调 invoke 命令，跳过完整 push channel 流程。
#
# 端到端真跑（用 BedtimeSource + chat 双窗 + 真 LLM）走 015 的
# `scripts/dev-pet-bubble-demo/run.sh`，本脚本不取代它，**互补**。
#
# 用法：./scripts/dev-bubble-spotcheck/run.sh
#
# 详见 docs/requirements/016-pet-bubble-independent-window/。
set -euo pipefail
cd "$(dirname "$0")/../.."

cat <<'EOF'
===> 016 bubble window dev spot-check（不依赖 bridge）

启动 frontend dev 后，按下面步骤操作：

  1. 等待 tauri dev 加载完成（pet 窗 + chat 窗都出现）
  2. 右键 pet 窗 → "检查"（dev 模式下 Tauri 默认允许）打开 bubble webview 的 devtools
     - 实际上 bubble window 默认 hidden，devtools 不可见。可以：
     - a) 临时改 frontend/src-tauri/tauri.conf.json 把 bubble window 的 visible 改成 true
        看到 bubble 窗后右键它检查
     - b) 或：从 pet 窗 devtools console 调 invoke 命令也行，下面 invoke 是同样可用的
  3. 在任一 webview devtools console 里跑：

       // 1. show / hide
       const inv = (await import('@tauri-apps/api/core')).invoke;
       await inv('show_bubble');            // bubble window 应出现并跟随 pet 主窗
       await inv('hide_bubble');            // bubble window 应消失

       // 2. 调 size
       await inv('show_bubble');
       await inv('set_bubble_size', { width: 240, height: 64 });   // MIN
       await inv('set_bubble_size', { width: 360, height: 480 });  // MAX
       await inv('set_bubble_size', { width: 100, height: 32 });   // 低于 MIN → 被 clamp 到 240×64
       await inv('set_bubble_size', { width: 999, height: 999 });  // 高于 MAX → 被 clamp 到 360×480

       // 3. 真正模拟一条主动轮气泡（不依赖 bridge / push channel）：
       //    从 pet webview 或 bubble webview devtools，直接操纵 store：
       const store = (await import('@/stores/petBubble')).usePetBubbleStore;
       store.setState({ phase: 'showing', current: { id: 't1', text: '测试气泡很短', sourceKind: 'cron:bedtime' } });
       // → bubble window 应自动 show（attachBubbleWindowSync 触发 invoke show_bubble）
       // → PetBubble 的 ResizeObserver 应触发 invoke set_bubble_size

       store.setState({ phase: 'showing', current: { id: 't2', text: '这是一段长文字'.repeat(20), sourceKind: 'cron:bedtime' } });
       // → 同一 phase 不重复 show；ResizeObserver 重新测得新尺寸 → set_bubble_size
       store.getState().expand();                  // 测 expand 状态
       store.setState({ phase: 'idle', current: null });  // → invoke hide_bubble

  4. 同时打开 macOS Activity Monitor / `tauri windows list`（如适用）确认 bubble window
     是独立 OS 级 window（不是 pet 主窗内 DOM）—— AC-2 的直接证据。

EOF

echo "===> 启动 frontend dev（pnpm tauri dev）..."
./scripts/frontend/dev.sh
