import { useEffect, useState } from "react";
import { ChevronDown, ChevronUp, Search } from "lucide-react";
import {
  Badge,
  Button,
  Input,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui";
import { listPersonas, listRecalls, recallProbe } from "./api";
import type { Layer, Persona, RecallTrace as RecallTraceType, RecallTraceItem } from "./api";

interface Props {
  personaId: string | null;
  onHitClick: (hit: { layer: Layer; source_ref: string }) => void;
}

export function RecallTracePanel({ personaId, onHitClick }: Props) {
  const [traces, setTraces] = useState<RecallTraceType[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [topK, setTopK] = useState(8);
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const [personas, setPersonas] = useState<Persona[]>([]);
  const [probePersonaId, setProbePersonaId] = useState<string | null>(personaId);

  useEffect(() => {
    void listPersonas()
      .then((list) => {
        setPersonas(list);
        if (list.length > 0 && !probePersonaId) {
          setProbePersonaId(list[0].id);
        }
      })
      .catch(() => setPersonas([]));
  }, []);

  useEffect(() => {
    if (personaId) {
      setProbePersonaId(personaId);
    }
  }, [personaId]);

  const fetchTraces = async () => {
    try {
      const list = await listRecalls(100);
      setTraces(list);
      setError(null);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      if (msg.includes("memory disabled")) {
        setError("记忆模块未启用（AGENT_BRIDGE_MEMORY_ENABLED=false）");
      } else {
        setError(msg);
      }
    }
  };

  useEffect(() => {
    void fetchTraces();
    const id = setInterval(() => void fetchTraces(), 5000);
    return () => clearInterval(id);
  }, []);

  const runProbe = async () => {
    if (!query.trim() || !probePersonaId) return;
    setLoading(true);
    setError(null);
    try {
      const res = await recallProbe(query, probePersonaId, topK);
      if (res.trace) {
        setTraces((prev) => [res.trace, ...prev]);
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      if (msg.includes("memory disabled")) {
        setError("记忆模块未启用（AGENT_BRIDGE_MEMORY_ENABLED=false）");
      } else {
        setError(msg);
      }
    } finally {
      setLoading(false);
    }
  };

  const toggleExpand = (idx: number) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(idx)) next.delete(idx);
      else next.add(idx);
      return next;
    });
  };

  return (
    <div className="flex flex-col h-full min-w-0">
      <div className="flex items-center gap-2 p-3 border-b border-border bg-surface/50">
        <Select
          value={probePersonaId ?? "__none__"}
          onValueChange={(v) => setProbePersonaId(v === "__none__" ? null : v)}
        >
          <SelectTrigger className="w-32">
            <SelectValue placeholder="选择 persona" />
          </SelectTrigger>
          <SelectContent>
            {personas.map((p) => (
              <SelectItem key={p.id} value={p.id}>
                {p.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        <Input
          className="flex-1 min-w-0"
          placeholder="输入 query 试探召回..."
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && void runProbe()}
          disabled={!probePersonaId}
          type="search"
        />
        <Input
          type="number"
          min={1}
          max={50}
          className="w-20"
          value={topK}
          onChange={(e) => setTopK(Number(e.target.value))}
          disabled={!probePersonaId}
        />
        <Button onClick={runProbe} disabled={!probePersonaId || loading || !query.trim()}>
          <Search className="size-4" />
          试一下
        </Button>
      </div>

      {error && <div className="px-3 py-2 text-sm text-danger bg-danger/10">{error}</div>}

      <div className="flex-1 overflow-auto p-3 space-y-3">
        {traces.map((trace, idx) => (
          <TraceCard
            key={idx}
            trace={trace}
            expanded={expanded.has(idx)}
            onToggle={() => toggleExpand(idx)}
            onHitClick={onHitClick}
          />
        ))}
        {traces.length === 0 && !loading && (
          <div className="py-8 text-center text-sm text-muted">
            暂无 recall trace；让 agent 自然召回或点上方"试一下"
          </div>
        )}
      </div>
    </div>
  );
}

function TraceCard({
  trace,
  expanded,
  onToggle,
  onHitClick,
}: {
  trace: RecallTraceType;
  expanded: boolean;
  onToggle: () => void;
  onHitClick: (hit: { layer: Layer; source_ref: string }) => void;
}) {
  const isProbe = trace.source === "probe";

  return (
    <div
      className={`rounded-lg border p-3 text-sm ${
        isProbe ? "border-accent bg-accent/5" : "border-border bg-surface/50"
      }`}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <Badge variant={isProbe ? "default" : "secondary"}>{trace.source}</Badge>
            <span className="text-xs text-muted">{formatTime(trace.timestamp)}</span>
          </div>
          <p className="mt-1 font-medium truncate" title={trace.query}>
            {trace.query}
          </p>
        </div>
        <Button variant="ghost" size="icon-sm" onClick={onToggle}>
          {expanded ? <ChevronUp className="size-4" /> : <ChevronDown className="size-4" />}
        </Button>
      </div>

      <div className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 text-xs text-muted">
        <span>persona: {trace.persona_id}</span>
        <span>top_k: {trace.top_k}</span>
        <span>gate: {trace.gate_decision}</span>
        <span>mode: {trace.gate_mode ?? "-"}</span>
        <span>pre/post: {trace.pinned_pre_gate}/{trace.pinned_post_gate}</span>
        <span>candidates: {trace.candidates_count} / ranked: {trace.ranked_count}</span>
      </div>

      {expanded && (
        <div className="mt-3 space-y-2 border-t border-border pt-2">
          {trace.items.map((item, i) => (
            <TraceItemRow key={i} item={item} onClick={() => onHitClick(item)} />
          ))}
          {trace.items.length === 0 && (
            <div className="text-xs text-muted">无命中条目</div>
          )}
        </div>
      )}
    </div>
  );
}

function TraceItemRow({
  item,
  onClick,
}: {
  item: RecallTraceItem;
  onClick: () => void;
}) {
  return (
    <Button
      variant="ghost"
      onClick={onClick}
      className="h-auto w-full justify-start rounded-md border border-border bg-bg p-2 hover:bg-surface/80"
    >
      <div className="w-full text-left">
        <div className="flex items-center gap-2 text-xs">
          <Badge variant="secondary">{item.layer}</Badge>
          <span className="text-muted">score {item.score.toFixed(3)}</span>
        </div>
        <p className="mt-1 text-sm line-clamp-2">{item.text}</p>
        <p className="text-xs text-muted truncate">{item.source_ref}</p>
      </div>
    </Button>
  );
}

function formatTime(iso: string) {
  try {
    return new Date(iso).toLocaleString("zh-CN");
  } catch {
    return iso;
  }
}
