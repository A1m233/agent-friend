import "@/lib/logger";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "@/styles/index.css";
import { registerSettingsListener } from "@/lib/settings/listen";
import { startVoiceStateSubscriber } from "@/stores/voice";
import { PetApp } from "./App";

registerSettingsListener();
startVoiceStateSubscriber();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <PetApp />
  </StrictMode>,
);
