/** Only these facts may cross the dedicated diagnostic boundary. Never pass DOM/debug details. */
export type HitReason =
  | "disabled"
  | "initial"
  | "dragging"
  | "dom-hit"
  | "sprite-hit"
  | "sprite-transparent"
  | "outside";

export interface CursorDiagnostic {
  sampleId: number;
  dprRevision: number;
  scale: number;
  scaleSource: "webview" | "fallback";
  physicalX: number;
  physicalY: number;
  originX: number;
  originY: number;
}

export interface InputCursor {
  x: number;
  y: number;
  diagnostic?: CursorDiagnostic;
}

export interface InputCorrelation {
  pageInstanceId: string;
  frontendSeq: number;
  monotonicMs: number;
  displayRevision: number;
  requestId?: number;
  actualDpr?: number;
  displayDpr?: number;
  sampleId?: number;
}

export type InputKind =
  | "page-start" | "page-end" | "display-observed"
  | "dpr-report" | "dpr-ack" | "hit-decision" | "hit-summary"
  | "cursor-observed" | "passthrough-request" | "passthrough-ack" | "coverage-gap";

export interface InputEvent extends InputCorrelation {
  source: "frontend";
  kind: InputKind;
  reason?: HitReason;
  outcome?: "ok" | "error";
  errorKind?: "invoke-failed";
  stage?: "request" | "ack";
  reportedDpr?: number;
  ignore?: boolean;
  commandIssued?: boolean;
  dragging?: boolean;
  alphaTested?: boolean;
  alphaHit?: boolean;
  dprRevision?: number;
  scale?: number;
  scaleSource?: "webview" | "fallback";
  physical?: { x: number; y: number };
  css?: { x: number; y: number };
  windowOrigin?: { x: number; y: number };
  spriteBounds?: { x: number; y: number; width: number; height: number };
  viewport?: { width: number; height: number };
  count?: number;
  firstSeq?: number;
  lastSeq?: number;
  droppedCount?: number;
}

export interface HitObservation {
  reason: HitReason;
  ignore: boolean;
  commandSent: boolean;
  dragging: boolean;
  cursor?: InputCursor;
  spriteBounds?: { x: number; y: number; w: number; h: number } | null;
  alphaTested?: boolean;
  alphaHit?: boolean;
}
