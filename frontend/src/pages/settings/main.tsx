import "@/lib/logger";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { initializePermissionSync } from "@/lib/permissions";
import "@/styles/index.css";
// 人格编辑复用对话页的 TDesign Chat 消息与 sender，必须加载同一份组件样式。
import "@tdesign-react/chat/es/style/index.js";
import { AppToaster } from "@/components/app/AppToaster";
import { registerSettingsListener } from "@/lib/settings/listen";
import { startSessionsChangedSubscriber } from "@/stores";
import { SettingsApp } from "./App";

registerSettingsListener();
startSessionsChangedSubscriber();

void initializePermissionSync();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <SettingsApp />
    <AppToaster />
  </StrictMode>,
);
