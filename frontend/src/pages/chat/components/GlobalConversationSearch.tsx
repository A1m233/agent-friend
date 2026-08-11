import {
  KBarAnimator,
  KBarPortal,
  KBarPositioner,
  KBarProvider,
  KBarSearch,
  useKBar,
  useRegisterActions,
  type Action,
  type ActionImpl,
} from "kbar";
import { Archive, MessageSquareText, Search } from "lucide-react";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactElement,
  type ReactNode,
} from "react";
import { toast } from "sonner";
import { Badge, Button, Spinner } from "@/components/ui";
import { sessionsApi } from "@/services";
import { useConversationStore } from "@/stores";
import type {
  SearchTextSegment,
  SessionSearchResponse,
  SessionSearchResult,
} from "@/types/search";
import { formatRelativeMessageTime } from "@/utils/formatRelativeMessageTime";

const SEARCH_DEBOUNCE_MS = 200;
const KBAR_LISTBOX_ID = "kbar-listbox";

const getKBarListboxItemId = (index: number) => `kbar-listbox-item-${index}`;

type SearchViewState =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "error" }
  | { status: "settled"; response: SessionSearchResponse };

export function GlobalConversationSearchProvider({ children }: { children: ReactNode }) {
  const options = useMemo(
    () => ({
      animations: { enterMs: 120, exitMs: 90 },
      disableScrollbarManagement: true,
      toggleShortcut: "$mod+f",
    }),
    [],
  );

  return (
    <KBarProvider options={options}>
      {children}
      <GlobalConversationSearch />
    </KBarProvider>
  );
}

function GlobalConversationSearch() {
  const { searchQuery } = useKBar((state) => ({ searchQuery: state.searchQuery }));
  const openSearchResult = useConversationStore((state) => state.openSearchResult);
  const [view, setView] = useState<SearchViewState>({ status: "idle" });
  const [retrySequence, setRetrySequence] = useState(0);
  const requestSequenceRef = useRef(0);
  const normalizedQuery = searchQuery.trim();

  useEffect(() => {
    const requestSequence = requestSequenceRef.current + 1;
    requestSequenceRef.current = requestSequence;
    if (!normalizedQuery) {
      setView({ status: "idle" });
      return;
    }

    setView({ status: "loading" });
    const timer = window.setTimeout(() => {
      void sessionsApi
        .search(normalizedQuery, { messageLimit: 50, titleLimit: 10 })
        .then((response) => {
          if (requestSequence !== requestSequenceRef.current) return;
          setView({ status: "settled", response });
        })
        .catch(() => {
          if (requestSequence !== requestSequenceRef.current) return;
          setView({ status: "error" });
        });
    }, SEARCH_DEBOUNCE_MS);

    return () => window.clearTimeout(timer);
  }, [normalizedQuery, retrySequence]);

  const response = view.status === "settled" ? view.response : null;
  const results = useMemo(
    () => response ? [...response.title_results, ...response.message_results] : [],
    [response],
  );
  const resultById = useMemo(
    () => new Map(results.map((result) => [result.result_id, result])),
    [results],
  );
  const handleOpenResult = useCallback(
    (result: SessionSearchResult) => {
      const query = response?.query ?? normalizedQuery;
      void openSearchResult(result, query).then((opened) => {
        if (!opened) {
          toast.error("这条搜索结果已经发生变化，请重新搜索。", {
            description: "目标对话可能已被删除或改写。",
          });
        }
      });
    },
    [normalizedQuery, openSearchResult, response?.query],
  );
  const actions = useMemo<Action[]>(
    () =>
      results.map((result) => ({
        id: result.result_id,
        name: result.session_title,
        subtitle: result.kind === "message" ? result.segments.map((item) => item.text).join("") : undefined,
        perform: () => handleOpenResult(result),
      })),
    [handleOpenResult, results],
  );
  useRegisterActions(actions, [actions]);

  const { registeredActions } = useKBar((state) => ({
    registeredActions: state.actions,
  }));
  const resultItems = useMemo<Array<ActionImpl | string>>(() => {
    if (!response) return [];
    const items: Array<ActionImpl | string> = [];
    const titleActions = response.title_results.flatMap((result) => {
      const action = registeredActions[result.result_id];
      return action ? [action] : [];
    });
    const messageActions = response.message_results.flatMap((result) => {
      const action = registeredActions[result.result_id];
      return action ? [action] : [];
    });
    if (titleActions.length > 0) items.push("对话", ...titleActions);
    if (messageActions.length > 0) items.push("消息", ...messageActions);
    return items;
  }, [registeredActions, response]);

  const total = response ? response.title_total + response.message_total : 0;
  const shown = response ? response.title_results.length + response.message_results.length : 0;
  const truncated = response ? shown < total : false;

  return (
    <KBarPortal>
      <KBarPositioner className="z-50 bg-fg/20">
        <KBarAnimator className="w-full max-w-3xl">
          <section
            role="dialog"
            aria-modal="true"
            aria-labelledby="global-conversation-search-title"
            aria-describedby="global-conversation-search-description"
            className="overflow-hidden rounded-xl border border-border bg-bg text-fg shadow-2xl"
          >
            <h2 id="global-conversation-search-title" className="sr-only">
              搜索全部对话
            </h2>
            <p id="global-conversation-search-description" className="sr-only">
              搜索普通和已归档对话的标题、可见消息与附件文件名。
            </p>
            <div className="relative border-b border-border">
              <Search
                aria-hidden
                className="pointer-events-none absolute left-4 top-1/2 size-5 -translate-y-1/2 text-muted"
              />
              <KBarSearch
                aria-label="搜索全部对话"
                className="w-full bg-transparent py-4 pl-12 pr-4 text-base text-fg outline-none placeholder:text-muted"
                defaultPlaceholder="搜索全部对话"
                maxLength={200}
              />
            </div>

            <div className="min-h-40" aria-live="polite">
              {view.status === "idle" ? <SearchHint /> : null}
              {view.status === "loading" ? <SearchLoading /> : null}
              {view.status === "error" ? (
                <SearchError onRetry={() => setRetrySequence((current) => current + 1)} />
              ) : null}
              {view.status === "settled" && total === 0 ? (
                <SearchEmpty query={response?.query ?? normalizedQuery} />
              ) : null}
              {view.status === "settled" && total > 0 ? (
                <>
                  <SearchResultsList
                    items={resultItems}
                    onRender={({ item, active }) => {
                      if (typeof item === "string") {
                        return (
                          <div className="px-4 pb-1 pt-3 text-xs font-medium text-muted">
                            {item}
                          </div>
                        );
                      }
                      const result = resultById.get(item.id);
                      return result ? (
                        <SearchResultRow result={result} active={active} />
                      ) : (
                        <div />
                      );
                    }}
                  />
                  {truncated ? (
                    <p className="border-t border-border px-4 py-3 text-xs text-muted">
                      共 {total} 条，当前展示前 {shown} 条，请继续输入缩小范围。
                    </p>
                  ) : null}
                </>
              ) : null}
            </div>

            <footer className="flex items-center justify-between border-t border-border bg-surface px-4 py-2 text-xs text-muted">
              <span>↑↓ 选择 · Enter 打开 · Esc 关闭</span>
              <span>搜索包含已归档对话</span>
            </footer>
          </section>
        </KBarAnimator>
      </KBarPositioner>
    </KBarPortal>
  );
}

interface SearchResultsListProps {
  items: Array<ActionImpl | string>;
  onRender: (params: { item: ActionImpl | string; active: boolean }) => ReactElement;
}

export function SearchResultsList({ items, onRender }: SearchResultsListProps) {
  const activeRef = useRef<HTMLDivElement>(null);
  const { query, search, currentRootActionId, activeIndex, options } = useKBar((state) => ({
    search: state.searchQuery,
    currentRootActionId: state.currentRootActionId,
    activeIndex: state.activeIndex,
  }));

  const firstSelectableIndex = useMemo(
    () => items.findIndex((item) => typeof item !== "string"),
    [items],
  );

  const findNextSelectableIndex = useCallback(
    (currentIndex: number, direction: -1 | 1) => {
      let nextIndex = currentIndex + direction;
      while (nextIndex >= 0 && nextIndex < items.length) {
        if (typeof items[nextIndex] !== "string") return nextIndex;
        nextIndex += direction;
      }
      return currentIndex;
    },
    [items],
  );

  const execute = useCallback(
    (item: ActionImpl) => {
      if (item.command) {
        item.command.perform(item);
        query.toggle();
      } else {
        query.setSearch("");
        query.setCurrentRootAction(item.id);
      }
      options.callbacks?.onSelectAction?.(item);
    },
    [options.callbacks, query],
  );

  useEffect(() => {
    if (firstSelectableIndex >= 0) query.setActiveIndex(firstSelectableIndex);
  }, [currentRootActionId, firstSelectableIndex, query, search]);

  useEffect(() => {
    if (firstSelectableIndex < 0) return;
    if (typeof items[activeIndex] === "string" || !items[activeIndex]) {
      query.setActiveIndex(firstSelectableIndex);
    }
  }, [activeIndex, firstSelectableIndex, items, query]);

  useEffect(() => {
    activeRef.current?.scrollIntoView({ block: "nearest" });
  }, [activeIndex]);

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.isComposing) return;
      if (event.key === "ArrowUp" || (event.ctrlKey && event.key === "p")) {
        event.preventDefault();
        event.stopPropagation();
        query.setActiveIndex((index) => findNextSelectableIndex(index, -1));
      } else if (event.key === "ArrowDown" || (event.ctrlKey && event.key === "n")) {
        event.preventDefault();
        event.stopPropagation();
        query.setActiveIndex((index) => findNextSelectableIndex(index, 1));
      } else if (event.key === "Enter") {
        event.preventDefault();
        event.stopPropagation();
        const item = items[activeIndex];
        if (item && typeof item !== "string") execute(item);
      }
    };

    window.addEventListener("keydown", handleKeyDown, { capture: true });
    return () => window.removeEventListener("keydown", handleKeyDown, { capture: true });
  }, [activeIndex, execute, findNextSelectableIndex, items, query]);

  return (
    <div id={KBAR_LISTBOX_ID} role="listbox" className="max-h-100 overflow-y-auto">
      {items.map((item, index) => {
        if (typeof item === "string") {
          return (
            <div key={`${item}-${index}`} role="presentation">
              {onRender({ item, active: false })}
            </div>
          );
        }

        const active = index === activeIndex;
        return (
          <div
            id={getKBarListboxItemId(index)}
            key={item.id}
            ref={active ? activeRef : null}
            role="option"
            aria-selected={active}
            onPointerMove={() => {
              if (!active) query.setActiveIndex(index);
            }}
            onPointerDown={() => query.setActiveIndex(index)}
            onClick={() => execute(item)}
          >
            {onRender({ item, active })}
          </div>
        );
      })}
    </div>
  );
}

function SearchResultRow({ result, active }: { result: SessionSearchResult; active: boolean }) {
  const occurredAt = result.kind === "title" ? result.updated_at : result.occurred_at;
  const role = result.kind === "message"
    ? result.role === "user" ? "你" : "助手"
    : "对话标题";
  return (
    <div
      className={`mx-2 flex gap-3 rounded-lg px-3 py-2.5 ${active ? "bg-surface-hover" : "bg-bg"}`}
    >
      <span className="mt-0.5 text-muted">
        {result.kind === "title" ? (
          <Search className="size-4" />
        ) : (
          <MessageSquareText className="size-4" />
        )}
      </span>
      <span className="min-w-0 flex-1">
        <span className="flex items-center gap-2">
          <span className="truncate text-sm font-medium">
            {result.kind === "title" ? (
              <HighlightedSegments segments={result.segments} />
            ) : (
              result.session_title
            )}
          </span>
          {result.archived ? (
            <Badge variant="secondary" className="gap-1 font-normal">
              <Archive />
              已归档
            </Badge>
          ) : null}
        </span>
        {result.kind === "message" ? (
          <span className="mt-1 block text-sm text-fg">
            <HighlightedSegments segments={result.segments} />
          </span>
        ) : null}
        <span className="mt-1 flex items-center gap-2 text-xs text-muted">
          <span>{role}</span>
          <span aria-hidden>·</span>
          <span>{formatRelativeMessageTime(occurredAt)}</span>
          {result.kind === "message" && result.matched_field === "attachment_name" ? (
            <>
              <span aria-hidden>·</span>
              <span>附件名</span>
            </>
          ) : null}
        </span>
      </span>
    </div>
  );
}

function HighlightedSegments({ segments }: { segments: SearchTextSegment[] }) {
  return segments.map((segment, index) =>
    segment.matched ? (
      <mark key={index} className="rounded-sm bg-search-hit px-0.5 text-fg">
        {segment.text}
      </mark>
    ) : (
      <span key={index}>{segment.text}</span>
    ),
  );
}

function SearchHint() {
  return (
    <div className="flex min-h-40 flex-col items-center justify-center gap-2 px-6 text-center">
      <Search className="size-6 text-muted" />
      <p className="text-sm text-fg">输入关键词，搜索所有对话</p>
      <p className="text-xs text-muted">包含标题、你和助手的可见消息，以及附件文件名。</p>
    </div>
  );
}

function SearchLoading() {
  return (
    <div className="flex min-h-40 items-center justify-center gap-2 text-sm text-muted" role="status">
      <Spinner />
      正在搜索所有对话…
    </div>
  );
}

function SearchEmpty({ query }: { query: string }) {
  return (
    <div className="flex min-h-40 flex-col items-center justify-center gap-2 px-6 text-center">
      <p className="text-sm text-fg">没有找到“{query}”</p>
      <p className="text-xs text-muted">试试更短的连续关键词。</p>
    </div>
  );
}

function SearchError({ onRetry }: { onRetry: () => void }) {
  return (
    <div className="flex min-h-40 flex-col items-center justify-center gap-3 px-6 text-center">
      <p className="text-sm text-fg">搜索暂时没有完成</p>
      <Button type="button" size="sm" variant="outline" onClick={onRetry}>
        重试
      </Button>
    </div>
  );
}
