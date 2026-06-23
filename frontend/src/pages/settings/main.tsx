import "@/lib/logger";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "@/styles/index.css";
import { initTheme } from "@/hooks/useTheme";
import { SettingsApp } from "./App";

// 在 React 挂载前应用保存的主题，避免首屏闪屏。
initTheme();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <SettingsApp />
  </StrictMode>,
);
