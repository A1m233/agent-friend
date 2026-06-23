import "@/lib/logger";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "@/styles/index.css";
// tdesign-chat 散件样式（仅对话窗加载；桌宠窗不引）。
import "@tdesign-react/chat/es/style/index.js";
import { registerSettingsListener } from "@/lib/settings/listen";
import { ChatApp } from "./App";

registerSettingsListener();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ChatApp />
  </StrictMode>,
);
