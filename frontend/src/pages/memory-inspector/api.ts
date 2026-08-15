import { BRIDGE_BASE_URL } from "@/constants";
import { ensureLocalServiceReady } from "@/services/runtime";

export interface Persona {
  id: string;
  name: string;
  source: string;
  description?: string;
}

export interface SemanticRow {
  kind: "ordinary";
  id: string;
  statement: string;
  persona_id: string;
  created_at: string;
  updated_at: string;
  importance: number;
  pinned: boolean;
  source: string;
  speaker_origin: string;
  memory_class: "core" | "priority" | "normal";
  fact_key: string | null;
  supersedes_id: string | null;
  valid_from: string | null;
  valid_until: string | null;
  provenance: string[];
  deleted_at: string | null;
  owner_user_id: string;
}

export interface CoreProfileEntry {
  kind: "core";
  id: string;
  owner_user_id: string;
  scope_kind: "user" | "persona";
  persona_id: string;
  field_key:
    | "user.preferred_name"
    | "user.current_residences"
    | "relationship.kind"
    | "relationship.agent_nickname";
  value: string;
  normalized_value: string;
  valid_from: string;
  valid_until: string | null;
  supersedes_id: string | null;
  provenance: string[];
  created_at: string;
  updated_at: string;
  deleted_at: string | null;
}

export type InspectorSemanticRow = SemanticRow | CoreProfileEntry;
export type SemanticKind = "all" | "core" | "ordinary";

export interface EpisodicRow {
  id: string;
  summary: string;
  source_ref: string;
  persona_id: string;
  occurred_at: string;
  created_at: string;
  importance: number;
  participants: string[];
  deleted_at: string | null;
  owner_user_id: string;
}

export type Layer = "core" | "episodic" | "semantic" | "pinned" | "utterance";
export type GateMode = "strict" | "lenient";
export type GateDecision = "disabled" | "pass-through" | "matched";
export type RecallSource = "natural" | "probe";

export interface RecallTraceItem {
  text: string;
  layer: Layer;
  source_ref: string;
  score: number;
}

export interface RecallTrace {
  timestamp: string;
  query: string;
  owner_user_id: string;
  persona_id: string;
  top_k: number;
  source: RecallSource;
  pinned_pre_gate: number;
  pinned_post_gate: number;
  gate_enabled: boolean;
  gate_mode: GateMode | null;
  gate_decision: GateDecision;
  candidates_count: number;
  ranked_count: number;
  items: RecallTraceItem[];
  intent: string | null;
  time_range: { since: string | null; until: string | null } | null;
  mode: "automatic" | "explicit" | "probe" | null;
  source_runs: Array<{
    source: string;
    candidate_count: number;
    limit: number;
    status: "ok" | "failed" | "skipped";
  }> | null;
  candidates: Array<{
    evidence_id: string;
    kind: Layer;
    source: string;
    keyword_rank: number | null;
    dense_rank: number | null;
    fused_score: number | null;
    decision: string;
    decision_reason: string;
  }> | null;
  budget: Record<string, number> | null;
  index_state: {
    keyword_ready: boolean;
    dense_status: string;
    active_generation: string | null;
    indexed_count?: number;
    total_count?: number;
    last_error?: string | null;
  } | null;
}

export interface SearchResultRow<T> {
  row: T;
  bm25: number;
}

export interface SearchResult {
  semantic: SearchResultRow<SemanticRow>[];
  episodic: SearchResultRow<EpisodicRow>[];
}

export interface RecallProbeResponse {
  rendered: string;
  items: Array<{ text: string; layer: Layer; source_ref: string; score: number }>;
  trace: RecallTrace;
}

async function fetchJson<T>(url: string, init?: RequestInit): Promise<T> {
  await ensureLocalServiceReady("bridge");
  const res = await fetch(`${BRIDGE_BASE_URL}${url}`, init);
  if (!res.ok) {
    const text = await res.text().catch(() => "unknown error");
    throw new Error(`${res.status} ${res.statusText}: ${text}`);
  }
  return res.json() as Promise<T>;
}

export async function listPersonas(): Promise<Persona[]> {
  return fetchJson<Persona[]>("/v1/personas");
}

export async function listSemantic(
  kind: SemanticKind = "ordinary",
  personaId: string | null = null,
  limit = 50,
  offset = 0,
): Promise<InspectorSemanticRow[]> {
  const params = new URLSearchParams({ kind, limit: String(limit), offset: String(offset) });
  if (personaId) params.set("persona_id", personaId);
  return fetchJson<InspectorSemanticRow[]>(`/v1/memory/semantic?${params.toString()}`);
}

export async function listEpisodic(
  personaId: string | null,
  limit = 50,
  offset = 0,
): Promise<EpisodicRow[]> {
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  if (personaId) params.set("persona_id", personaId);
  return fetchJson<EpisodicRow[]>(`/v1/memory/episodic?${params.toString()}`);
}

export async function searchMemory(
  q: string,
  layer: "semantic" | "episodic" | "both",
  personaId: string | null,
  limit = 50,
): Promise<SearchResult> {
  const params = new URLSearchParams({ q, layer, limit: String(limit) });
  if (personaId) params.set("persona_id", personaId);
  return fetchJson<SearchResult>(`/v1/memory/search?${params.toString()}`);
}

export async function listRecalls(limit = 100): Promise<RecallTrace[]> {
  return fetchJson<RecallTrace[]>(`/v1/memory/recalls?limit=${limit}`);
}

export async function recallProbe(
  query: string,
  personaId: string,
  topK = 8,
): Promise<RecallProbeResponse> {
  return fetchJson<RecallProbeResponse>("/v1/memory/recall-probe", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query, persona_id: personaId, top_k: topK }),
  });
}
