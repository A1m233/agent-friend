import { useCallback, useEffect, useRef, useState } from "react";
import { ArrowLeft, ArrowRight, BookOpen, Check, Copy } from "lucide-react";
import petIcon from "../../../src-tauri/icons/128x128@2x.png";
import { openUrl } from "@tauri-apps/plugin-opener";
import { Button, ScrollArea, Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui";
import { useCopyFeedback } from "@/hooks/use-copy-feedback";
import { setSetting, useSetting, type ModelSelection } from "@/lib/settings";
import { useConnectionStore, useRuntimeCredentialStore } from "@/stores";
import { CustomConnections } from "@/pages/settings/components/CustomConnections";
import { CredentialEditor } from "@/pages/settings/components/RuntimeConfigPanel";
import { ModelSelectionRows } from "@/pages/settings/components/ModelSelectionRows";
import { PersonaPanel } from "@/pages/settings/components/PersonaPanel";
import { CompanionshipSetting } from "@/pages/settings/components/CompanionshipSetting";
import { SettingsGroup } from "@/pages/settings/components/SettingsGroup";
import { NotificationSettings } from "@/pages/settings/components/NotificationSettings";
import { guidePrompt, missingStep, readStatus, selectModel, type SetupStatus, type Step } from "./setup";
import { SpeechInput } from "./SpeechInput";

const titles: Record<Step, string> = {
  welcome: "嗨！我是你的 agent-friend ！",
  connect: "连接模型服务",
  chat: "选择默认对话模型",
  memory: "选择默认记忆模型",
  ready: "这下可以开聊啦！",
  voice: "用说话代替打字",
  persona: "和伙伴一起调整人格",
  companionship: "自主陪伴与通知",
};
const descriptions: Record<Step, string> = {
  welcome: "见到你真高兴。咱们先把聊天用的模型连上，以后哪天碰上什么有意思的小事，都可以来找我聊两句。",
  connect: "咱们先连上你想用的模型服务。如果用的是自己的服务地址，选「自定义连接」就好。服务商可能会按用量收费。",
  chat: "以后每次开一段新对话，我都会用这里选的模型回你。挑个你喜欢的，以后也能换。",
  memory: "我想记住你在意的事，下次聊起来还能接得上。用刚才的对话模型帮我整理记忆就行，也可以单独选一个。",
  ready: "想先聊两句？现在就可以关掉这个向导，从桌宠打开对话，照常使用 AF。点「继续」可以看看自主陪伴、人格和语音输入，这些以后随时都能在「设置」里调整。",
  voice: "有时候打字跟不上想说的话。填好火山语音的凭据，以后点聊天框里的麦克风，就能把话转成文字。",
  persona: "喜欢我直说，还是想让我先听你说完？在下面聊聊就好。我会把改好的版本给你看，由你决定要不要用。",
  companionship: "有时我也想来找你聊两句。你可以决定要不要我主动联系。至于哪些消息要提醒你，可以单独选。",
};
const basicSteps: Step[] = ["connect", "chat", "memory"];
const personalSteps: Step[] = ["companionship", "persona", "voice"];
const steps: Step[] = ["welcome", ...basicSteps, "ready", ...personalSteps];

export default function Onboarding({ active, navigation, onStart }: { active: boolean; navigation?: { step: Step; requestId: number }; onStart: () => Promise<void> }) {
  const [step, setStep] = useState<Step>("welcome");
  const [visited, setVisited] = useState<Step[]>([]);
  const [history, setHistory] = useState<Step[]>([]);
  const [status, setStatus] = useState<SetupStatus | null>(null);
  const [channel, setChannel] = useState<"deepseek" | "kimi" | "custom">("deepseek");
  const [chat, setChat] = useState<ModelSelection>();
  const [memory, setMemory] = useState<ModelSelection>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const { status: copyStatus, copy } = useCopyFeedback(guidePrompt);
  const [chatSetting] = useSetting("chatModel");
  const [memorySetting] = useSetting("memoryModel");
  const [connections] = useSetting("modelConnections");
  const bridge = useConnectionStore((s) => s.status);
  const groups = useRuntimeCredentialStore((s) => s.groups);
  const latest = useRef(0);
  const busyRef = useRef(false);
  const heading = useRef<HTMLHeadingElement>(null);
  const surface = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!active) return;
    if (surface.current) surface.current.scrollTop = 0;
    heading.current?.focus({ preventScroll: true });
  }, [step, active]);
  const refresh = useCallback(async () => {
    const request = ++latest.current;
    try {
      const next = await readStatus();
      if (request === latest.current) {
        setStatus(next);
        setError("");
      }
      return next;
    } catch (e) {
      if (request === latest.current) {
        setStatus(null);
        setError(String(e));
      }
      throw e;
    }
  }, []);
  useEffect(() => {
    if (!active) return;
    const update = () => {
      void refresh().catch(() => {});
    };
    update();
    window.addEventListener("focus", update);
    return () => {
      ++latest.current;
      window.removeEventListener("focus", update);
    };
  }, [active, refresh, bridge, groups, connections, chatSetting, memorySetting]);
  const handledRequest = useRef(0);
  useEffect(() => {
    const requested = navigation?.step;
    if (!active || !requested || !status || !navigation || handledRequest.current === navigation.requestId)
      return;
    handledRequest.current = navigation.requestId;
    const missing = missingStep(status);
    const target = missing && steps.indexOf(missing) < steps.indexOf(requested) ? missing : requested;
    if (step !== target) {
      setHistory((old) => [...old, step]);
      setVisited((old) => (old.includes(target) ? old : [...old, target]));
      setStep(target);
    }
  }, [active, navigation, status, step]);
  function go(next: Step) {
    if (next !== step) setHistory((old) => [...old, step]);
    setVisited((old) => (old.includes(next) ? old : [...old, next]));
    setStep(next);
    setError("");
  }
  function back() {
    const prev = history.at(-1);
    if (prev) {
      setHistory((old) => old.slice(0, -1));
      setStep(prev);
      setError("");
    }
  }
  async function run(action: () => Promise<void>) {
    if (busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    setError("");
    try {
      await action();
    } catch (e) {
      setError(String(e));
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  }
  const available = status?.models ?? [];
  const choice = (draft?: ModelSelection, saved?: ModelSelection): ModelSelection | undefined =>
    [draft, saved, available[0]].find((s) => s && available.some((m) => m.model === s.model && m.provider === s.provider));
  const currentChat = choice(chat, status?.chatModel);
  const currentMemory = choice(memory, status?.memoryReady ? status.memoryModel : status?.chatModel);
  const stepSelection = step === "chat" ? currentChat : currentMemory;
  const optional = personalSteps.includes(step);
  const currentSteps = optional ? personalSteps : basicSteps;
  const lastStep = step === steps.at(-1);
  async function finish() {
    const next = await refresh();
    const missing = missingStep(next);
    if (missing) {
      go(missing);
      setError("配置有变化，请确认当前缺少的项目。");
      return;
    }
    await onStart();
    setStep("welcome");
    setVisited([]);
    setHistory([]);
    setChat(undefined);
    setMemory(undefined);
  }
  async function forward() {
    if (step === "welcome") {
      const current =
        status?.models.find((m) => m.provider === status.chatModel.provider) ?? status?.models[0];
      if (current && current.provider !== "openrouter") setChannel(current.provider);
    }
    if (step === "connect") {
      const next = await refresh();
      const selected = next.models.filter((m) => m.provider === channel);
      if (!selected.length) throw new Error("这个服务还没有可用模型，请保存并应用连接，或选择已连接的服务。");
      const previous = history.at(-1);
      if (previous === "memory") {
        if (!selected.some((m) => m.model === currentMemory?.model)) setMemory(selected[0]);
      } else if (!selected.some((m) => m.model === currentChat?.model)) {
        setChat(selected[0]);
      }
      if (previous === "chat" || previous === "memory") {
        back();
        return;
      }
    }
    if (step === "chat" || step === "memory") {
      const selection = step === "chat" ? currentChat : currentMemory;
      if (!selection) throw new Error("请先连接模型服务，再确认模型。");
      await selectModel(step, selection);
      const now = await refresh();
      if (step === "memory") {
        const missing = missingStep(now);
        if (missing) {
          go(missing);
          return;
        }
        await setSetting("onboardingCompleted", true);
      }
    }
    if (step === "ready") {
      go(missingStep(await refresh()) ?? personalSteps[0]);
      return;
    }
    if (lastStep) {
      await finish();
      return;
    }
    go(steps[steps.indexOf(step) + 1]);
  }
  const editorState = useRuntimeCredentialStore.getState();
  return (
    <section aria-label="新手引导" className="flex h-full flex-col bg-bg text-fg">
      {step !== "welcome" && (
        <header className="mx-auto flex w-full max-w-2xl shrink-0 items-center justify-between gap-2 px-6 pt-8 pb-3 text-sm text-muted">
          <span className="flex items-center gap-2"><BookOpen className="size-4" />新手引导</span>
          {step !== "ready" && (
            <span>{optional ? "个性化" : "基础配置"} · {currentSteps.indexOf(step) + 1} / {currentSteps.length}</span>
          )}
        </header>
      )}
      <ScrollArea className="min-h-0 flex-1" viewportRef={surface} viewportClassName="vertical-page-viewport">
        <div className={`mx-auto grid max-w-2xl gap-5 px-6 ${step === "welcome" ? "min-h-screen content-center py-8" : "pt-2 pb-8"}`}>
          {step === "welcome" ? (
            <>
              <div className="flex flex-wrap items-center gap-6 py-4">
                <img src={petIcon} alt="" className="size-28 shrink-0 object-contain" />
                <div className="min-w-48 flex-1">
                  <h1 ref={heading} tabIndex={-1} className="text-3xl font-semibold">{titles.welcome}</h1>
                  <p className="mt-3 leading-relaxed">{descriptions.welcome}</p>
                  <Button className="mt-4" disabled={busy} onClick={() => void run(forward)}>
                    一起设置 <ArrowRight />
                  </Button>
                </div>
              </div>
              <section aria-labelledby="agent-help" className="grid gap-3 border-t border-border py-5">
                <h2 id="agent-help" className="text-lg font-semibold">也可以请你的 Agent 帮忙</h2>
                <p className="text-sm text-muted">把下面这段话发给你的 Agent，它就知道怎么帮你配置 agent-friend 了。</p>
                <p className="select-all whitespace-pre-line break-words rounded-xl bg-surface px-4 py-3 text-sm leading-relaxed">
                  {guidePrompt}
                </p>
                <div className="flex flex-wrap items-center gap-3">
                  <Button variant="outline" onClick={() => void copy()}>
                    {copyStatus === "copied" ? <Check className="text-success" /> : <Copy />} 复制提示词
                  </Button>
                  <span role="status" className={copyStatus === "failed" ? "text-sm text-muted" : "sr-only"}>
                    {copyStatus === "copied" && "已复制"}
                    {copyStatus === "failed" && "没能自动复制。选中上面这段话，就可以手动复制。"}
                  </span>
                </div>
              </section>
            </>
          ) : (
            <div>
              <h1 ref={heading} tabIndex={-1} className="text-2xl font-semibold">{titles[step]}</h1>
              <p className="mt-2 text-sm leading-relaxed text-muted">{descriptions[step]}</p>
            </div>
          )}
          {visited.includes("connect") && (
            <div hidden={step !== "connect"} className="space-y-3">
              <Select value={channel} onValueChange={(v) => setChannel(v as typeof channel)}>
                <SelectTrigger aria-label="模型服务">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="deepseek">DeepSeek</SelectItem>
                  <SelectItem value="kimi">Kimi</SelectItem>
                  <SelectItem value="custom">自定义连接（BYOK）</SelectItem>
                </SelectContent>
              </Select>
              {(["deepseek", "kimi"] as const).map((id) => (
                <div key={id} hidden={channel !== id}>
                  <Button
                    variant="link"
                    onClick={() =>
                      void openUrl(
                        id === "deepseek"
                          ? "https://platform.deepseek.com/api_keys"
                          : "https://platform.kimi.ai/console/api-keys",
                      )
                    }
                  >
                    获取 API Key
                  </Button>
                  <CredentialEditor
                    group={id}
                    snapshot={groups.find((g) => g.group === id)}
                    apply={editorState.applyGroup}
                    confirm={editorState.confirmSave}
                    cancel={editorState.cancelOperation}
                    retry={editorState.retryApply}
                    retryValidation={editorState.retryValidation}
                  />
                </div>
              ))}
              <div hidden={channel !== "custom"}>
                <CustomConnections />
              </div>
            </div>
          )}
          {(step === "chat" || step === "memory") && (
            <div className="space-y-2 rounded-lg border border-border bg-surface/50 p-5">
              {stepSelection ? (
                <ModelSelectionRows
                  key={step}
                  selection={stepSelection}
                  purpose={step}
                  modelLabel={step === "chat" ? "默认模型" : "记忆模型"}
                  onChange={step === "chat" ? setChat : setMemory}
                  allowedModels={available.map((m) => m.model)}
                  disabled={busy}
                />
              ) : <p className="text-sm text-muted">请先连接模型服务。</p>}
            </div>
          )}
          {(step === "chat" || step === "memory") && (
            <div className="flex flex-wrap items-center gap-2">
              {step === "memory" && (
                <Button variant="outline" disabled={busy || !status?.chatReady} onClick={() => setMemory(status?.chatModel)}>
                  沿用对话模型
                </Button>
              )}
              <Button variant="link" disabled={busy} onClick={() => go("connect")}>管理模型连接</Button>
            </div>
          )}
          {step === "ready" && (
            <dl className="grid gap-2">
              {(["chatModel", "memoryModel"] as const).map((key) => (
                <div key={key} className="flex justify-between gap-3 border-b border-border py-2">
                  <dt>{key === "chatModel" ? "默认对话模型" : "默认记忆模型"}</dt>
                  <dd>{available.find((m) => m.model === status?.[key].model)?.label ?? "待确认"}</dd>
                </div>
              ))}
            </dl>
          )}
          {visited.includes("voice") && (
            <div hidden={step !== "voice"}>
              <SpeechInput />
            </div>
          )}
          {visited.includes("persona") && <PersonaPanel embedded active={active && step === "persona"} />}
          {visited.includes("companionship") && (
            <div hidden={step !== "companionship"} className="grid gap-4">
              <SettingsGroup title="活动与联系" description="开启自主陪伴后，我在后台也会用到模型，服务商可能会收费。">
                <CompanionshipSetting />
              </SettingsGroup>
              <SettingsGroup title="桌宠通知">
                <NotificationSettings />
              </SettingsGroup>
            </div>
          )}
          {step !== "welcome" && error && (
            <div role="alert" className="text-sm text-danger">
              {error}{" "}
              <Button
                variant="link"
                onClick={() =>
                  void run(async () => {
                    await refresh();
                  })
                }
              >
                重新检查
              </Button>
            </div>
          )}
        </div>
      </ScrollArea>
      {step !== "welcome" && (
        <footer className="shrink-0 border-t border-border">
          <div className="mx-auto flex max-w-2xl flex-wrap items-center gap-2 px-6 py-4">
            {history.length > 0 && (
              <Button className="mr-auto" variant="ghost" disabled={busy} onClick={back}>
                <ArrowLeft />
                上一步
              </Button>
            )}
            <Button disabled={busy} onClick={() => void run(forward)}>
              {lastStep ? "完成并开始使用" : "继续"}
            </Button>
          </div>
        </footer>
      )}
    </section>
  );
}
