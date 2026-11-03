import { invoke } from "@tauri-apps/api/core";
import { toast } from "sonner";
import { companionshipApi } from "@/services/api/companionship";

export async function openInnerActivity() {
  try {
    const { session_id } = await companionshipApi.innerSession();
    await invoke("open_chat_session", { sessionId: session_id });
  } catch {
    toast.error("暂时无法打开内心活动");
  }
}
