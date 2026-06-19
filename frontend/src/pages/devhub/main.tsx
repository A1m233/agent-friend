import "@/lib/logger";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "@/styles/index.css";
import { DevHub } from "./App";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <DevHub />
  </StrictMode>,
);
