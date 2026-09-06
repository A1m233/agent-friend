import "@/lib/logger";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { initializePermissionSync } from "@/lib/permissions";
import { installProductContextMenuPolicy } from "@/lib/contextMenu";
import "@/styles/index.css";
import { registerSettingsListener } from "@/lib/settings/listen";
import { BubbleApp } from "./App";

installProductContextMenuPolicy();
const settingsReady = registerSettingsListener();

void initializePermissionSync();

void settingsReady.then(() => {
  createRoot(document.getElementById("root")!).render(
    <StrictMode>
      <BubbleApp />
    </StrictMode>,
  );
});
