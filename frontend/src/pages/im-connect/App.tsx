import { useEffect, useState } from "react";
import { listen } from "@tauri-apps/api/event";

import { IMConnectPanel } from "@/components/im/IMConnectPanel";
import { isTauri } from "@/utils/tauri";

const EVENT_WINDOW_SHOWN = "window://shown";
const EVENT_WINDOW_HIDDEN = "window://hidden";

export function IMConnectApp() {
  const [active, setActive] = useState(() => !isTauri());

  useEffect(() => {
    if (!isTauri()) return;
    let cancelled = false;
    let unlistenShown: (() => void) | null = null;
    let unlistenHidden: (() => void) | null = null;

    void listen(EVENT_WINDOW_SHOWN, () => setActive(true)).then((unlisten) => {
      if (cancelled) unlisten();
      else unlistenShown = unlisten;
    });
    void listen(EVENT_WINDOW_HIDDEN, () => setActive(false)).then((unlisten) => {
      if (cancelled) unlisten();
      else unlistenHidden = unlisten;
    });

    return () => {
      cancelled = true;
      unlistenShown?.();
      unlistenHidden?.();
    };
  }, []);

  return <IMConnectPanel active={active} />;
}
