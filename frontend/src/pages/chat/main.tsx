import "@/lib/logger";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { initializePermissionSync } from "@/lib/permissions";
import "@/styles/index.css";
// tdesign-chat 散件样式（仅对话窗加载；桌宠窗不引）。
import "@tdesign-react/chat/es/style/index.js";
import { AppToaster } from "@/components/app/AppToaster";
import { installProductContextMenuPolicy } from "@/lib/contextMenu";
import { registerSettingsListener } from "@/lib/settings/listen";
import { startSessionsChangedSubscriber } from "@/stores";
import { startVoiceStateSubscriber } from "@/stores/voice";
import { ChatApp } from "./App";
import "./layout.css";

installProductContextMenuPolicy();
registerSettingsListener();
startSessionsChangedSubscriber();
startVoiceStateSubscriber();

void initializePermissionSync();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ChatApp />
    <AppToaster />
  </StrictMode>,
);
