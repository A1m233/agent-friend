import "@/lib/logger";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "@/styles/index.css";
import { registerSettingsListener } from "@/lib/settings/listen";
import {
  startVoiceOwnerCommandListener,
  startVoiceStateSubscriber,
  useVoiceStore,
} from "@/stores/voice";
import { VoiceCallApp } from "./App";

registerSettingsListener();
startVoiceStateSubscriber();
startVoiceOwnerCommandListener();

window.addEventListener("beforeunload", () => {
  if (useVoiceStore.getState().isOwner) {
    void useVoiceStore.getState().hangUp();
  }
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <VoiceCallApp />
  </StrictMode>,
);
