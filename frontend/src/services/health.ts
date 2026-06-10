import { http } from "./http";

/** 探测 bridge 是否存活（GET /healthz）。用于打通基础请求层。 */
export async function checkHealth(): Promise<boolean> {
  try {
    const data = await http.get<unknown, { status?: string }>("/healthz");
    return data?.status === "ok";
  } catch {
    return false;
  }
}
