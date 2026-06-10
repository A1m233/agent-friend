import { useEffect, useState } from "react";
import { checkHealth } from "@/services";

/**
 * dev hub：仅开发期浏览器调试用（非 Tauri 窗口）。
 * 提供到两个窗口入口的链接，并探测一次 bridge 健康状态以打通基础请求层。
 */
export function DevHub() {
  const [bridge, setBridge] = useState<"checking" | "online" | "offline">("checking");

  useEffect(() => {
    checkHealth().then((ok) => setBridge(ok ? "online" : "offline"));
  }, []);

  return (
    <div className="grid min-h-screen place-items-center bg-bg text-fg">
      <div className="flex flex-col items-center gap-4">
        <h1 className="text-lg font-semibold">agent-friend · dev hub</h1>
        <div className="flex gap-4">
          <a className="text-accent underline" href="/pet.html">桌宠窗</a>
          <a className="text-accent underline" href="/chat.html">对话窗</a>
        </div>
        <span className="text-sm text-muted">
          bridge:{" "}
          <span className={bridge === "online" ? "text-accent" : "text-muted"}>{bridge}</span>
        </span>
      </div>
    </div>
  );
}
