import "@/lib/logger";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "@/styles/index.css";
import { registerSettingsListener } from "@/lib/settings/listen";
import { Toaster } from "@/components/ui";
import { Live2DDebuggerApp } from "./App";

registerSettingsListener();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <Live2DDebuggerApp />
    <Toaster />
  </StrictMode>,
);
