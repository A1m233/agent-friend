import "@/lib/logger";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { initializePermissionSync } from "@/lib/permissions";
import { installProductContextMenuSuppression } from "@/lib/contextMenu";
import "@/styles/index.css";
import { registerSettingsListener } from "@/lib/settings/listen";
import { BubbleApp } from "./App";

installProductContextMenuSuppression();
registerSettingsListener();

void initializePermissionSync();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BubbleApp />
  </StrictMode>,
);
