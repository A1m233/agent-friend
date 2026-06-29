import "@/lib/logger";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "@/styles/index.css";
import { AppToaster } from "@/components/app/AppToaster";
import { registerSettingsListener } from "@/lib/settings/listen";
import { Live2DDebuggerApp } from "./App";

registerSettingsListener();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <Live2DDebuggerApp />
    <AppToaster />
  </StrictMode>,
);
