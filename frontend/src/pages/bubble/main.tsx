import "@/lib/logger";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "@/styles/index.css";
import { BubbleApp } from "./App";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BubbleApp />
  </StrictMode>,
);
