import "@/lib/logger";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "@/styles/index.css";
import { registerSettingsListener } from "@/lib/settings/listen";
import { DevHub } from "./App";

registerSettingsListener();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <DevHub />
  </StrictMode>,
);
