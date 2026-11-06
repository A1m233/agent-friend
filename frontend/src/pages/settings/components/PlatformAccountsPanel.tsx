import { useCallback, useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui";
import { platformAccountsApi, type BilibiliAccount } from "@/services/api/platformAccounts";
import { SettingsGroup } from "./SettingsGroup";
import { SettingsRow } from "./SettingsRow";

const labels: Record<BilibiliAccount["state"], string> = {
  anonymous: "未登录", credential_present: "已保存登录信息，尚未验证", authenticated: "已登录",
  expired: "登录已失效", unavailable: "暂时无法验证账号",
};

export function PlatformAccountsPanel() {
  const [account, setAccount] = useState<BilibiliAccount | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const epoch = useRef(0);
  const refresh = useCallback(async () => {
    const current = ++epoch.current;
    try {
      const value = await platformAccountsApi.account(true);
      if (current === epoch.current) { setAccount(value); setError(null); }
    } catch { if (current === epoch.current) setError("暂时无法读取账号状态，请重试"); }
  }, []);
  useEffect(() => { void refresh(); return () => { epoch.current++; }; }, [refresh]);
  const loginId = account?.login_id;
  const running = account?.login_state === "running";
  useEffect(() => {
    if (!running) return;
    let active = true;
    let inFlight = false;
    const timer = window.setInterval(async () => {
      if (inFlight) return;
      inFlight = true;
      const current = epoch.current;
      try {
        const value = await platformAccountsApi.account();
        if (!active || current !== epoch.current || value.login_id !== loginId) return;
        setAccount(value);
        if (value.login_state === "completed") void refresh();
        if (value.login_state === "failed") setError("登录未完成，请重新扫码");
      } catch { if (active) setError("暂时无法获取登录进度"); }
      finally { inFlight = false; }
    }, 1500);
    return () => { active = false; window.clearInterval(timer); };
  }, [running, loginId, refresh]);
  const act = async (action: "login" | "logout") => {
    const current = ++epoch.current;
    setBusy(true); setError(null);
    try { const value = await platformAccountsApi[action](); if (current === epoch.current) setAccount(value); }
    catch { if (current === epoch.current) setError(action === "logout" ? "退出未完成，请重试" : "暂时无法开始登录"); }
    finally { if (current === epoch.current) setBusy(false); }
  };
  return <SettingsGroup title="B 站账号" description="登录 AF 使用的账号。AF 可以按需查看视频评论、回复和 @ 你；你的个人账号无需在这里登录。">
    <SettingsRow label="AF 的账号"><span className="text-sm">{account ? labels[account.state] : "正在读取…"}</span></SettingsRow>
    {account?.state === "authenticated" && <div className="flex items-center gap-3">
      {account.avatar?.startsWith("https://") && <img src={account.avatar} referrerPolicy="no-referrer" className="size-10 rounded-full" alt="AF 的 B 站头像" />}
      <p className="text-sm">{account.name} · UID {account.uid}</p>
    </div>}
    {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
    {running && <div className="flex flex-col items-center gap-3">
      <p className="text-sm text-muted">使用 AF 的 B 站账号扫码</p>
      {account?.has_challenge && loginId ? <img className="size-48 rounded-md" src={platformAccountsApi.challenge(loginId)} alt="B 站登录二维码" /> : <p className="text-sm text-muted">正在准备二维码…</p>}
    </div>}
    <div className="flex gap-2">
      {!running && <Button disabled={busy || account?.state === "authenticated"} onClick={() => void act("login")}>扫码登录</Button>}
      {(running || (account && account.state !== "anonymous")) && <Button variant="outline" disabled={busy} onClick={() => void act("logout")}>{running ? "取消登录" : "退出账号"}</Button>}
      <Button variant="ghost" disabled={busy || running} onClick={() => void refresh()}>刷新状态</Button>
    </div>
  </SettingsGroup>;
}
