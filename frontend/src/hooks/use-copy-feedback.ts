import { useEffect, useRef, useState } from "react";

export function useCopyFeedback(text: string) {
  const [status, setStatus] = useState<"idle" | "copied" | "failed">("idle");
  const attempt = useRef(0);
  const timer = useRef<number | undefined>(undefined);

  useEffect(() => {
    setStatus("idle");
    return () => {
      attempt.current++;
      window.clearTimeout(timer.current);
    };
  }, [text]);

  async function copy() {
    if (!text) return;
    const current = ++attempt.current;
    window.clearTimeout(timer.current);
    try {
      await navigator.clipboard.writeText(text);
      if (current !== attempt.current) return;
      setStatus("copied");
      timer.current = window.setTimeout(() => setStatus("idle"), 1200);
    } catch {
      if (current === attempt.current) setStatus("failed");
    }
  }

  return { status, copy };
}
