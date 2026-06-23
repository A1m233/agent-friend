import { useCallback, useEffect, useRef, useState } from "react";
import { Pin, Search } from "lucide-react";
import { useVirtualizer } from "@tanstack/react-virtual";
import {
  Badge,
  Input,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  Skeleton,
  Tabs,
  TabsList,
  TabsTrigger,
  TextEllipsis,
} from "@/components/ui";
import {
  listEpisodic,
  listPersonas,
  listSemantic,
  searchMemory,
} from "./api";
import type { EpisodicRow, Layer, Persona, SemanticRow } from "./api";

const PAGE_SIZE = 50;

interface Props {
  personaId: string | null;
  onPersonaChange: (id: string | null) => void;
  highlight: { layer: Layer; source_ref: string } | null;
}

export function MemoryList({ personaId, onPersonaChange, highlight }: Props) {
  const [personas, setPersonas] = useState<Persona[]>([]);
  const [activeTab, setActiveTab] = useState<"semantic" | "episodic">("semantic");
  const [query, setQuery] = useState("");
  const [semanticRows, setSemanticRows] = useState<SemanticRow[]>([]);
  const [episodicRows, setEpisodicRows] = useState<EpisodicRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [offset, setOffset] = useState(0);
  const [hasMore, setHasMore] = useState(true);

  const scrollRef = useRef<HTMLDivElement>(null);

  const rows = activeTab === "semantic" ? semanticRows : episodicRows;

  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => 96,
    overscan: 5,
    getItemKey: (index) => rows[index].id,
  });

  const virtualItems = virtualizer.getVirtualItems();

  const loadPersonas = useCallback(async () => {
    try {
      const list = await listPersonas();
      setPersonas(list);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  const resetAndLoad = useCallback(() => {
    setOffset(0);
    setHasMore(true);
    setSemanticRows([]);
    setEpisodicRows([]);
  }, []);

  const loadMore = useCallback(
    async (append = false) => {
      setLoading(true);
      setError(null);
      try {
        const currentOffset = append ? offset : 0;
        if (activeTab === "semantic") {
          const rows = await listSemantic(PAGE_SIZE, currentOffset);
          setSemanticRows((prev) => (append ? [...prev, ...rows] : rows));
          setHasMore(rows.length === PAGE_SIZE);
        } else {
          const rows = await listEpisodic(personaId, PAGE_SIZE, currentOffset);
          setEpisodicRows((prev) => (append ? [...prev, ...rows] : rows));
          setHasMore(rows.length === PAGE_SIZE);
        }
        setOffset(currentOffset + PAGE_SIZE);
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setLoading(false);
      }
    },
    [activeTab, offset, personaId],
  );

  const runSearch = useCallback(
    async (q: string) => {
      setLoading(true);
      setError(null);
      try {
        if (!q.trim()) {
          resetAndLoad();
          if (activeTab === "semantic") {
            const rows = await listSemantic(PAGE_SIZE, 0);
            setSemanticRows(rows);
            setHasMore(rows.length === PAGE_SIZE);
          } else {
            const rows = await listEpisodic(personaId, PAGE_SIZE, 0);
            setEpisodicRows(rows);
            setHasMore(rows.length === PAGE_SIZE);
          }
          setOffset(PAGE_SIZE);
          return;
        }
        const result = await searchMemory(
          q,
          activeTab,
          activeTab === "episodic" ? personaId : null,
          PAGE_SIZE,
        );
        if (activeTab === "semantic") {
          setSemanticRows(result.semantic.map((r) => r.row));
        } else {
          setEpisodicRows(result.episodic.map((r) => r.row));
        }
        setHasMore(false);
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setLoading(false);
      }
    },
    [activeTab, personaId, resetAndLoad],
  );

  useEffect(() => {
    void loadPersonas();
  }, [loadPersonas]);

  useEffect(() => {
    resetAndLoad();
    void loadMore(false);
  }, [activeTab, personaId]);

  const searchTimer = useRef<number | null>(null);

  useEffect(() => {
    if (searchTimer.current) {
      window.clearTimeout(searchTimer.current);
    }
    searchTimer.current = window.setTimeout(() => {
      void runSearch(query);
    }, 250);
    return () => {
      if (searchTimer.current) {
        window.clearTimeout(searchTimer.current);
      }
    };
  }, [query, runSearch]);

  const onSearchKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") {
      if (searchTimer.current) {
        window.clearTimeout(searchTimer.current);
      }
      void runSearch(query);
    }
  };

  const onScroll = () => {
    if (loading || !hasMore || query.trim()) return;
    const target = scrollRef.current;
    if (!target) return;
    const bottom = target.scrollHeight - target.scrollTop - target.clientHeight < 40;
    if (bottom) {
      void loadMore(true);
    }
  };

  useEffect(() => {
    if (!highlight) return;
    const targetIndex = rows.findIndex((r) => r.id === highlight.source_ref);
    if (targetIndex >= 0) {
      virtualizer.scrollToIndex(targetIndex, { align: "center" });
    }
  }, [highlight, rows, virtualizer]);

  return (
    <div className="flex flex-col h-full min-w-0">
      <div className="flex items-center gap-2 p-3 border-b border-border bg-surface/50">
        <Select
          value={personaId ?? "__all__"}
          onValueChange={(v) => onPersonaChange(v === "__all__" ? null : v)}
        >
          <SelectTrigger className="w-40">
            <SelectValue placeholder="选择 persona" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="__all__">全部</SelectItem>
            {personas.map((p) => (
              <SelectItem key={p.id} value={p.id}>
                {p.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        <Tabs value={activeTab} onValueChange={(v) => setActiveTab(v as typeof activeTab)}>
          <TabsList>
            <TabsTrigger value="semantic">Semantic</TabsTrigger>
            <TabsTrigger value="episodic">Episodic</TabsTrigger>
          </TabsList>
        </Tabs>

        <div className="relative flex-1 min-w-0">
          <Search className="absolute left-2 top-1/2 -translate-y-1/2 size-4 text-muted" />
          <Input
            className="pl-8"
            placeholder="FTS 关键字搜索..."
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={onSearchKeyDown}
          />
        </div>
      </div>

      {activeTab === "semantic" && (
        <div className="px-3 py-1.5 text-xs text-muted bg-bg border-b border-border">
          Semantic 记忆跨 persona 共享，selector 不影响此列表
        </div>
      )}

      {error && <div className="px-3 py-2 text-sm text-danger bg-danger/10">{error}</div>}

      <div ref={scrollRef} className="flex-1 overflow-auto p-3" onScroll={onScroll}>
        {rows.length === 0 && !loading && (
          <div className="py-8 text-center text-sm text-muted">
            无 {activeTab === "semantic" ? "semantic" : "episodic"} 记忆
          </div>
        )}

        {rows.length > 0 && (
          <div
            className="relative w-full"
            style={{ height: `${virtualizer.getTotalSize()}px` }}
          >
            <div
              style={{
                position: "absolute",
                top: 0,
                left: 0,
                width: "100%",
                transform: `translateY(${virtualItems[0]?.start ?? 0}px)`,
              }}
            >
              {virtualItems.map((virtualRow) => {
                const row = rows[virtualRow.index];
                return (
                  <div
                    key={virtualRow.key}
                    data-index={virtualRow.index}
                    ref={virtualizer.measureElement}
                  >
                    {activeTab === "semantic" ? (
                      <SemanticCard
                        row={row as SemanticRow}
                        highlighted={highlight?.layer === "semantic" && highlight.source_ref === row.id}
                      />
                    ) : (
                      <EpisodicCard
                        row={row as EpisodicRow}
                        highlighted={highlight?.layer === "episodic" && highlight.source_ref === row.id}
                      />
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {loading && (
          <div className="space-y-2 mt-2">
            <Skeleton className="h-20 w-full" />
            <Skeleton className="h-20 w-full" />
          </div>
        )}

        {!loading && hasMore && !query.trim() && rows.length > 0 && (
          <div className="py-2 text-center text-xs text-muted">向下滚动加载更多</div>
        )}
      </div>
    </div>
  );
}

function SemanticCard({ row, highlighted }: { row: SemanticRow; highlighted: boolean }) {
  return (
    <div
      data-source-ref={row.id}
      className={`rounded-lg border p-3 text-sm ${
        highlighted ? "border-accent bg-accent/10" : "border-border bg-surface/50"
      }`}
    >
      <div className="flex items-start justify-between gap-2">
        <p className="font-medium leading-snug">{row.statement}</p>
        {row.pinned && (
          <Badge variant="default" className="shrink-0">
            <Pin className="size-3" />
            pinned
          </Badge>
        )}
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-muted">
        <Badge variant="secondary">{row.source}</Badge>
        <Badge variant="secondary">{row.speaker_origin}</Badge>
        <span>importance {row.importance.toFixed(2)}</span>
        <span>·</span>
        <span>{formatTime(row.created_at)}</span>
      </div>
    </div>
  );
}

function EpisodicCard({ row, highlighted }: { row: EpisodicRow; highlighted: boolean }) {
  return (
    <div
      data-source-ref={row.id}
      className={`rounded-lg border p-3 text-sm ${
        highlighted ? "border-accent bg-accent/10" : "border-border bg-surface/50"
      }`}
    >
      <p className="font-medium leading-snug">{row.summary}</p>
      <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-muted">
        <Badge variant="secondary" className="flex-1 min-w-0">
          <TextEllipsis className="flex-1 min-w-0">{row.source_ref}</TextEllipsis>
        </Badge>
        <span>importance {row.importance.toFixed(2)}</span>
        <span>·</span>
        <span>{formatTime(row.occurred_at)}</span>
      </div>
    </div>
  );
}

function formatTime(iso: string) {
  try {
    return new Date(iso).toLocaleString("zh-CN");
  } catch {
    return iso;
  }
}
