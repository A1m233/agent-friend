/**
 * 非 shadcn，自写：Radix Portal 的 Activity 生命周期适配，不替代浮层原语。
 * React 19.2 的嵌套 Portal 不随 Activity 隐藏（react/react#35000）。
 * 按上游建议管理容器：layout cleanup 同步移除，恢复时挂回；保留原语状态。
 */
import { useLayoutEffect, useState, type ReactNode } from "react";

export function PortalHost({ container, children }: {
  container?: Element | DocumentFragment | null;
  children: (host: HTMLDivElement) => ReactNode;
}) {
  const [host] = useState(() => {
    if (typeof document === "undefined") return null;
    const element = document.createElement("div");
    element.dataset.uiPortalHost = "";
    return element;
  });

  useLayoutEffect(() => {
    if (!host) return;
    const parent = container ?? document.body;
    parent.appendChild(host);
    return () => host.remove();
  }, [container, host]);

  return host ? children(host) : null;
}
