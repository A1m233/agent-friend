/** 是否运行在 Tauri 桌面环境（区别于浏览器 web 调试）。 */
export function isTauri(): boolean {
  return (
    typeof window !== "undefined" &&
    "isTauri" in window &&
    Boolean((window as { isTauri?: boolean }).isTauri)
  );
}
