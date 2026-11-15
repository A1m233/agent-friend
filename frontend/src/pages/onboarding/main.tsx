import "@/lib/logger";
import "@/styles/index.css";
import "@tdesign-react/chat/es/style/index.js";
import { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { AppToaster } from "@/components/app/AppToaster";
import { TooltipProvider } from "@/components/ui";
import { useModelCatalogStore } from "@/lib/model-config";
import { installProductContextMenuPolicy } from "@/lib/contextMenu";
import { useSetting } from "@/lib/settings";
import { registerSettingsListener } from "@/lib/settings/listen";
import { startRuntimeCredentialSync, useConnectionStore } from "@/stores";
import Onboarding from "./Onboarding";
import type { Step } from "./setup";

function SetupAssistant() {
  const [navigation, setNavigation] = useState<{ step: Step; requestId: number }>();
  const online = useConnectionStore((state) => state.status === "online");
  const [connections] = useSetting("modelConnections");
  useEffect(() => {
    if (!online) return;
    const reload = () => { void useModelCatalogStore.getState().load(true); };
    reload();
    window.addEventListener("focus", reload);
    return () => window.removeEventListener("focus", reload);
  }, [online, connections]);
  useEffect(() => {
    let disposed = false;
    const readStep = async () => {
      const step = await invoke<Step | null>("take_onboarding_step");
      if (step && !disposed) setNavigation((old) => ({ step, requestId: (old?.requestId ?? 0) + 1 }));
    };
    const listener = listen("window://shown", () => { void readStep(); });
    void listener.then(readStep);
    const stop = startRuntimeCredentialSync();
    const ping = useConnectionStore.getState().ping;
    void ping();
    const timer = window.setInterval(() => void ping(), 3000);
    return () => {
      disposed = true;
      void listener.then((unlisten) => unlisten());
      stop();
      window.clearInterval(timer);
    };
  }, []);
  return (
    <TooltipProvider>
      <main className="h-screen">
        <Onboarding active navigation={navigation} onStart={() => invoke<void>("finish_onboarding")} />
        <AppToaster />
      </main>
    </TooltipProvider>
  );
}

installProductContextMenuPolicy();
void registerSettingsListener().then(() => {
  createRoot(document.getElementById("root")!).render(<SetupAssistant />);
});
