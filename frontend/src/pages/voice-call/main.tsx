import "@/lib/logger";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { initializePermissionSync } from "@/lib/permissions";
import { installProductContextMenuPolicy } from "@/lib/contextMenu";
import "@/styles/index.css";
import { registerSettingsListener } from "@/lib/settings/listen";
import {
  startVoiceOwnerCommandListener,
  startVoiceStateSubscriber,
  useVoiceStore,
} from "@/stores/voice";
import { VoiceCallApp } from "./App";

installProductContextMenuPolicy();
const settingsReady = registerSettingsListener();
startVoiceStateSubscriber();
startVoiceOwnerCommandListener();

window.addEventListener("beforeunload", () => {
  if (useVoiceStore.getState().isOwner) {
    void useVoiceStore.getState().hangUp();
  }
});

void initializePermissionSync();

void settingsReady.then(() => {
  createRoot(document.getElementById("root")!).render(
    <StrictMode>
      <VoiceCallApp />
    </StrictMode>,
  );
});
