import { listen } from "@tauri-apps/api/event";
import { create } from "zustand";
import { cancelDiagnosticJob, getDiagnosticJob, type DiagnosticJob } from "@/services/diagnostics";

interface State {
  job: DiagnosticJob | null;
  error: string | null;
  refresh: () => Promise<void>;
  cancel: () => Promise<void>;
}

export function acceptDiagnosticJob(current: DiagnosticJob | null, next: DiagnosticJob): DiagnosticJob {
  if (current && (next.ordinal < current.ordinal || (next.ordinal === current.ordinal && next.version < current.version))) return current;
  return next;
}

function receive(job: DiagnosticJob) {
  useDiagnosticExportStore.setState((state) => ({ job: acceptDiagnosticJob(state.job, job), error: null }));
}

export const useDiagnosticExportStore = create<State>((set, get) => ({
  job: null,
  error: null,
  async refresh() {
    try { const job = await getDiagnosticJob(); if (job) receive(job); }
    catch (error) { set({ error: String(error) }); }
  },
  async cancel() {
    const job = get().job;
    if (!job) return;
    try { receive(await cancelDiagnosticJob(job.id)); }
    catch (error) { set({ error: String(error) }); }
  },
}));

export function startDiagnosticExportSync(): () => void {
  let stopped = false;
  const unlisten = listen<DiagnosticJob>("diagnostic-export://changed", (event) => {
    if (!stopped) receive(event.payload);
  });
  // Register first, then query; versions prevent a delayed snapshot reverting an event.
  void unlisten.then(() => { if (!stopped) void useDiagnosticExportStore.getState().refresh(); })
    .catch((error) => useDiagnosticExportStore.setState({ error: String(error) }));
  const interval = setInterval(() => {
    if (!stopped) void useDiagnosticExportStore.getState().refresh();
  }, 1000);
  return () => {
    stopped = true;
    clearInterval(interval);
    void unlisten.then((stop) => stop()).catch(() => {});
  };
}
