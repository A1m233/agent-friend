import {
  useEffect,
  useRef,
  useState,
  type RefObject,
} from "react";
import * as PIXI from "pixi.js";
import { invoke } from "@tauri-apps/api/core";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { isTauri } from "@/utils/tauri";

/**
 * Win spike (ADR 0004 §5.2 / spike-tauri-win-overlay-research §6.4) ·
 * 整屏 transparent overlay + PixiJS WebGL rAF 验证 demo。
 *
 * **触发**:URL `?spike=win-overlay` → `pet/App.tsx` 顶部 dispatch 到本组件。
 * **范围**:仅 spike 验证,不是产品代码。沿 macOS spike §5.5.7 同款 pattern(StrictMode
 * 双 mount 处理 + PIXI v8 destroy + 1Hz invoke `spike_win_log_perf` 把 fps 数据回 Rust log)。
 *
 * **Win 工程师跑法**:
 *   1. `pnpm tauri dev` 起开发模式
 *   2. 浏览器访问 `http://localhost:1420/pet.html?spike=win-overlay`(或 pet 窗自动加载
 *      pet.html 时手动改 hash/query)
 *   3. 看 Tauri 终端的 `spike-win-perf: fps=XX min=XX p50=XXms p95=XXms n=XXX` 行,
 *      30+ 秒稳态判 §3 通过条件
 *
 * **观察项**(spike doc §5.7 必验清单 + §6.4.1 复现 cfg 表):
 *   - fps p50:对照 macOS spike §7.6 段 B 30fps(macOS NSPanel WKWebView OS-level cap),
 *     理论 Win Chromium WebView2 无 cap → 应 ≥ 55fps
 *   - GDI 漏速(对照 wry#1691):1Hz fps invoke 不应让 cursor 持续 over webview 触发
 *     mouse movement → 但整屏 = cursor 任何移动都过 webview surface,务必 24h 长跑
 *   - tauri#9373 复现:本 demo = 整屏 + canvas 拉满 + rAF,正是 #9373 触发模式
 */
interface PerfHud {
  fps: number;
  p50: number;
  p95: number;
  minFps: number;
  samples: number;
}

function usePixiSprite(stageRef: RefObject<HTMLDivElement | null>) {
  const [hud, setHud] = useState<PerfHud | null>(null);

  useEffect(() => {
    const stage = stageRef.current;
    if (!stage) return;

    const frames: number[] = [];
    let lastT = performance.now();
    let app: PIXI.Application | null = null;
    let intervalId: number | null = null;
    let cancelled = false;

    void (async () => {
      const pixi = new PIXI.Application();
      await pixi.init({
        // 透明背景,让 Tauri transparent 窗的 DWM blur-behind region 透到底
        backgroundAlpha: 0,
        resizeTo: stage,
        antialias: true,
        autoDensity: true,
        resolution: window.devicePixelRatio || 1,
      });
      // StrictMode 双 mount 防泄漏(沿 macOS spike §5.5.7 同款 cancelled flag pattern)
      if (cancelled) {
        pixi.destroy(true, { children: true, texture: true });
        return;
      }
      app = pixi;
      stage.appendChild(pixi.canvas);

      // 测试 sprite(产品代码替换为 Live2DModel)
      const g = new PIXI.Graphics();
      g.rect(-40, -40, 80, 80)
        .fill({ color: 0x6688ff })
        .stroke({ width: 4, color: 0xffffff });
      pixi.stage.addChild(g);

      pixi.ticker.add(() => {
        const now = performance.now();
        const dt = now - lastT;
        lastT = now;
        frames.push(dt);
        if (frames.length > 600) frames.shift();
        g.x = pixi.screen.width / 2;
        g.y = pixi.screen.height / 2;
        g.rotation += 0.02;
      });

      // 1Hz 算 fps p50 / p95 / min + invoke Rust log(让 Win 工程师在终端直接看)
      intervalId = window.setInterval(() => {
        if (frames.length === 0) return;
        const sorted = [...frames].sort((a, b) => a - b);
        const sum = sorted.reduce((acc, v) => acc + v, 0);
        const fps = Math.round((frames.length * 1000) / sum);
        const p50 = Math.round((sorted[Math.floor(sorted.length * 0.5)] ?? 0) * 100) / 100;
        const p95 = Math.round((sorted[Math.floor(sorted.length * 0.95)] ?? 0) * 100) / 100;
        const max = sorted[sorted.length - 1] ?? 0;
        const minFps = max > 0 ? Math.round((1000 / max) * 10) / 10 : 0;
        const samples = frames.length;
        setHud({ fps, p50, p95, minFps, samples });
        if (isTauri()) {
          void invoke("spike_win_log_perf", {
            msg: `fps=${fps} min=${minFps} p50=${p50}ms p95=${p95}ms n=${samples}`,
          });
        }
      }, 1000);
    })();

    return () => {
      cancelled = true;
      if (intervalId !== null) window.clearInterval(intervalId);
      if (app) {
        try {
          if (stage.contains(app.canvas)) stage.removeChild(app.canvas);
        } catch {
          // ignore: dom may already be detached
        }
        app.destroy(true, { children: true, texture: true });
      }
    };
  }, [stageRef]);

  return hud;
}

/**
 * 整屏 transparent overlay 形态下的 root 容器:fixed inset-0 撑满 webview viewport,
 * Rust setup hook `#[cfg(target_os = "windows")]` 已把 pet 窗 set_size 到 monitors union
 * (mac 端 cfg gate 不触发,pet 窗仍是 240×320,本组件渲染时也按 viewport 撑)。
 */
export function PetSpikeWinOverlayApp() {
  const stageRef = useRef<HTMLDivElement | null>(null);
  const hud = usePixiSprite(stageRef);

  // spike 模式整屏 cursor 穿透:启动时 set true(让整屏 webview 不拦截鼠标,
  // 鼠标全穿透到下层桌面);unmount 时 set false 兜底。
  //
  // 注:这是 spike 阶段最简化处理 —— 不接入 60Hz Rust cursor polling + hit-test toggle
  // (那是真做 Live2D 接入需求时的 plumbing,本 spike 不验)。整屏 cursor 穿透 = 用户看
  // 不到 fps HUD 之外的任何交互;HUD 本身 pointer-events:none,显示但不响应,符合预期。
  useEffect(() => {
    if (!isTauri()) return;
    void getCurrentWindow().setIgnoreCursorEvents(true);
    return () => {
      void getCurrentWindow().setIgnoreCursorEvents(false);
    };
  }, []);

  return (
    <div
      id="pet-stage"
      ref={stageRef}
      className="fixed inset-0 overflow-hidden bg-transparent"
    >
      {hud && (
        <div className="pointer-events-none fixed left-2 top-2 rounded bg-black/60 px-2 py-1 font-mono text-xs text-white">
          fps={hud.fps} min={hud.minFps} p50={hud.p50}ms p95={hud.p95}ms n={hud.samples}
        </div>
      )}
    </div>
  );
}
