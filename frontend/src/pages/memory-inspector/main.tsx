import "@/lib/logger";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { initializePermissionSync } from "@/lib/permissions";
import "@/styles/index.css";
import { registerSettingsListener } from "@/lib/settings/listen";
import { MemoryInspectorApp } from "./App";

registerSettingsListener();

void initializePermissionSync();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <MemoryInspectorApp />
  </StrictMode>,
);
